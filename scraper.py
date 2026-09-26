import asyncio
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from config import (
    SSU_JOB_LIST_URL,
    SSU_JOB_DETAIL_URL,
    SSU_JOB_CATEGORY_CODES,
    SSU_PATH_LIST_URL,
)

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

_COMPETENCIES = ["창의", "융합", "공동체", "의사소통", "리더십", "글로벌"]
_PER_PAGE = 10
_MAX_PAGES_PER_CATEGORY = 100
_PATH_MAX_PAGES = 100
_PATH_PUBLIC_LIST_URL = SSU_PATH_LIST_URL
_SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
_PATH_LIST_COLUMNS = (
    "번호", "년도", "학기", "운영부서", "프로그램명",
    "신청기간", "교육기간", "모집정원", "진행상태",
)


class ScrapeSourceError(RuntimeError):
    pass


class SsuScraper:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0, headers=_HEADERS, follow_redirects=True,
        )
        self._http_client: httpx.AsyncClient | None = None
        self._scrape_lock = asyncio.Lock()
        self.last_scrape_report: dict[str, dict] = {}

    async def close(self):
        await self.client.aclose()
        if self._http_client:
            await self._http_client.aclose()

    async def scrape_all(self) -> list[dict]:
        programs = []
        self.last_scrape_report = {}
        successful_sources = 0

        try:
            job_programs = await self._scrape_job_center()
            programs.extend(job_programs)
            successful_sources += 1
            self.last_scrape_report["job_center"] = {
                "status": "success", "count": len(job_programs),
            }
        except Exception as error:
            logger.exception("SSU Job collection failed")
            self.last_scrape_report["job_center"] = {
                "status": "failed", "count": 0, "error": str(error),
            }

        try:
            path_programs = await self._scrape_ssu_path()
            programs.extend(path_programs)
            successful_sources += 1
            self.last_scrape_report["ssu_path"] = {
                "status": "success", "count": len(path_programs),
            }
        except Exception as error:
            logger.exception("SSU-PATH collection failed")
            self.last_scrape_report["ssu_path"] = {
                "status": "failed", "count": 0, "error": str(error),
            }

        if successful_sources == 0:
            raise ScrapeSourceError("All configured program sources failed")

        return programs

    async def _scrape_job_center(self) -> list[dict]:
        programs = []
        for category_code, category in SSU_JOB_CATEGORY_CODES.items():
            seen_pages = set()
            for page in range(1, _MAX_PAGES_PER_CATEGORY + 1):
                page_programs = await self._scrape_job_page(
                    page, category_code=category_code, category=category,
                )
                if not page_programs:
                    break
                fingerprint = tuple(
                    (program.get("detail_url"), program.get("title"))
                    for program in page_programs
                )
                if fingerprint in seen_pages:
                    raise ScrapeSourceError(
                        f"Repeated SSU Job page for category {category_code}: {page}"
                    )
                seen_pages.add(fingerprint)
                programs.extend(page_programs)
                if len(page_programs) < _PER_PAGE:
                    break
            else:
                raise ScrapeSourceError(
                    f"SSU Job page limit reached for category {category_code}"
                )
        return programs

    async def _scrape_job_page(
        self, page: int = 1, category_code: str = "0000", category: str = "기타",
    ) -> list[dict]:
        data = {
            "currentPageNo": str(page),
            "operYySh": str(datetime.now(_SEOUL_TIMEZONE).year),
            "prgmClsCdSh": category_code,
        }
        try:
            response = await self.client.post(SSU_JOB_LIST_URL, data=data)
            response.raise_for_status()
            programs = self._parse_job_programs(response.text)
            for program in programs:
                program["category"] = category
            return programs
        except Exception as error:
            raise ScrapeSourceError(
                f"Failed SSU Job category {category_code} page {page}: {error}"
            ) from error

    def _parse_job_programs(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("div.desc_wrap")
        programs = []
        for card in cards:
            try:
                program = self._extract_job_program(card)
                if program and len(program.get("title", "")) > 5:
                    program["source"] = "job_center"
                    programs.append(program)
            except Exception as e:
                logger.warning(f"Failed to parse job card: {e}")
        return programs

    def _extract_job_program(self, card: Tag) -> dict | None:
        title_el = card.select_one("a.detailBtn span.tit")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        status = ""
        status_el = card.select_one("div.label_box span")
        if status_el:
            status = status_el.get_text(strip=True)
        elif card.parent:
            status_el = card.parent.select_one("div.img_wrap span")
            if status_el:
                status = status_el.get_text(strip=True)

        major_items = card.select("ul.major_type li")
        department = major_items[0].get_text(strip=True) if len(major_items) > 0 else ""
        program_type = major_items[1].get_text(strip=True) if len(major_items) > 1 else ""

        desc_el = card.select_one("p.desc")
        description = desc_el.get_text(strip=True) if desc_el else ""

        apply_start, apply_end = "", ""
        edu_start, edu_end = "", ""
        target = ""

        for dl in card.select("div.info_wrap dl"):
            dt = dl.select_one("dt")
            dd = dl.select_one("dd")
            if not dt or not dd:
                continue
            label = dt.get_text(strip=True)
            value = dd.get_text(strip=True)
            if label == "신청기간":
                apply_start, apply_end = self._split_date_range(value)
            elif label in ("교육기간", "운영기간"):
                edu_start, edu_end = self._split_date_range(value)
            elif label == "신청대상":
                target = value

        competency = ",".join(c for c in _COMPETENCIES if c in card.get_text())

        detail_url = ""
        detail_btn = card.select_one("a.detailBtn")
        if detail_btn:
            params_raw = detail_btn.get("data-params", "{}")
            try:
                params = json.loads(params_raw)
                enc_seq = params.get("encSddpbSeq", "")
                if enc_seq:
                    detail_url = f"{SSU_JOB_DETAIL_URL}?encSddpbSeq={enc_seq}"
            except json.JSONDecodeError:
                pass

        return {
            "title": title,
            "category": self._infer_category(title, description),
            "status": status,
            "department": department,
            "program_type": program_type,
            "description": description,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "edu_start": edu_start,
            "edu_end": edu_end,
            "target": target,
            "competency": competency,
            "detail_url": detail_url,
        }

    @staticmethod
    def _infer_category(title: str, description: str = "") -> str:
        text = f"{title} {description}".lower()
        category_keywords = (
            ("공공인재양성반", ("공공인재",)),
            ("채용설명회/채용상담", ("채용설명회", "채용상담", "잡페어", "채용박람회")),
            ("서포터즈/홍보대사", ("서포터즈", "홍보대사")),
            ("공모전/경진대회", ("공모전", "경진대회", "해커톤")),
            ("소모임/동아리", ("소모임", "동아리")),
            ("전공탐색프로그램", ("전공탐색", "전공 탐색")),
            ("상담/멘토링/코칭", ("상담", "멘토링", "멘토-멘티", "코칭", "클리닉")),
            ("특강/워크숍", ("특강", "워크숍", "세미나", "캠프", "아카데미")),
            ("진로탐색프로그램", ("진로탐색", "진로 탐색", "직무탐색", "직무 탐색")),
        )
        for category, keywords in category_keywords:
            if any(keyword in text for keyword in keywords):
                return category
        return "기타"

    async def _scrape_ssu_path(self) -> list[dict]:
        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                timeout=30.0, headers=_HEADERS, follow_redirects=False,
            )

        # SSU-PATH publishes this external-user list without authentication.
        # The login page now uses SSO or an encrypted external-user form; it is
        # unrelated to this public endpoint and does not expose a CSRF token.
        response = await self._http_client.get(_PATH_PUBLIC_LIST_URL)
        self._validate_path_content_response(response)
        programs = self._parse_path_programs(response.text)

        page_parameter, current_page, last_page = self._path_pagination_info(
            response.text,
        )
        if current_page != 1:
            raise ScrapeSourceError(
                f"SSU-PATH first response was page {current_page}, expected page 1"
            )
        if last_page > _PATH_MAX_PAGES:
            raise ScrapeSourceError(
                f"SSU-PATH page limit exceeded: {last_page} pages"
            )
        for page in range(2, last_page + 1):
            page_response = await self._http_client.get(
                _PATH_PUBLIC_LIST_URL,
                params={page_parameter or "currentPageNo": str(page)},
            )
            self._validate_path_content_response(page_response)
            _parameter, returned_page, returned_last_page = (
                self._path_pagination_info(page_response.text)
            )
            if returned_page != page or returned_last_page != last_page:
                raise ScrapeSourceError(
                    "SSU-PATH pagination response did not match the requested page "
                    f"({page}; received {returned_page} of {returned_last_page})"
                )
            page_programs = self._parse_path_programs(page_response.text)
            if not page_programs:
                raise ScrapeSourceError(
                    f"SSU-PATH page {page} was empty before page {last_page}"
                )
            programs.extend(page_programs)

        logger.info("SSU-PATH list response: %s", response.status_code)
        return programs

    def _validate_path_content_response(self, response: httpx.Response):
        if response.is_redirect:
            location = response.headers.get("location", "")
            raise ScrapeSourceError(
                f"SSU-PATH program list redirected unexpectedly: {location}"
            )
        response.raise_for_status()
        if not self._has_path_list_markup(response.text):
            raise ScrapeSourceError("SSU-PATH response is not a public program list")

    @staticmethod
    def _has_path_list_markup(html: str) -> bool:
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.t_list")
        if not table:
            return False
        headers = tuple(
            cell.get_text(" ", strip=True)
            for cell in table.select("thead th")
        )
        return headers == _PATH_LIST_COLUMNS

    @staticmethod
    def _path_pagination_info(html: str) -> tuple[str | None, int, int]:
        soup = BeautifulSoup(html, "lxml")
        page_list = soup.select_one("ul.page_list")
        current_page = soup.select_one("form#baseForm input[name='currentPageNo']")
        page_summary = soup.select_one("ul.tab_bottom")
        summary_text = page_summary.get_text(" ", strip=True) if page_summary else ""
        summary = re.search(r"페이지\s+(\d+)\s*/\s*(\d+)", summary_text)

        if not current_page and not page_list and not page_summary:
            return None, 1, 1
        if not current_page:
            raise ScrapeSourceError("SSU-PATH pagination omitted currentPageNo")

        current_value = current_page.get("value", "")
        if not current_value.isdigit():
            raise ScrapeSourceError("SSU-PATH currentPageNo was not numeric")
        current_number = int(current_value)

        if summary:
            summary_current = int(summary.group(1))
            last_number = int(summary.group(2))
            if summary_current != current_number:
                raise ScrapeSourceError(
                    "SSU-PATH page summary disagrees with currentPageNo"
                )
        else:
            page_numbers = []
            if page_list:
                for link in page_list.select("a[onclick]"):
                    page_match = re.search(
                        r"global\.dialog\.page\((\d+)\)",
                        link.get("onclick", ""),
                    )
                    if page_match:
                        page_numbers.append(int(page_match.group(1)))
            if page_numbers:
                last_number = max(page_numbers)
            elif page_list or page_summary:
                raise ScrapeSourceError(
                    "SSU-PATH pagination omitted its page summary"
                )
            else:
                last_number = current_number

        if last_number < current_number:
            raise ScrapeSourceError("SSU-PATH currentPageNo exceeded its last page")
        return current_page.get("name"), current_number, last_number

    @staticmethod
    def _path_pagination(html: str) -> tuple[str | None, int]:
        parameter, _current_page, last_page = SsuScraper._path_pagination_info(html)
        return parameter, last_page

    def _parse_path_programs(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        programs = []

        table = soup.select_one("table.t_list")
        if not table:
            raise ScrapeSourceError("SSU-PATH public list table was not found")
        for card in table.select("tbody tr"):
            cells = card.select("td")
            if not cells:
                continue
            if len(cells) == 1 and cells[0].has_attr("colspan"):
                continue
            if len(cells) != len(_PATH_LIST_COLUMNS):
                raise ScrapeSourceError(
                    "SSU-PATH row did not match the observed 9-column public list"
                )
            program = self._extract_path_program(card)
            if not program or not program.get("title"):
                raise ScrapeSourceError(
                    "SSU-PATH row had no program title; refusing partial results"
                )
            program["source"] = "ssu_path"
            programs.append(program)
        return programs

    def _extract_path_program(self, card: Tag) -> dict | None:
        cells = card.select("td")
        if len(cells) < 9:
            return None

        title = cells[4].get_text(" ", strip=True)
        if not title:
            raise ScrapeSourceError("SSU-PATH row had an empty program title")

        apply_start, apply_end = self._split_date_range(
            cells[5].get_text(" ", strip=True),
        )
        edu_start, edu_end = self._split_date_range(
            cells[6].get_text(" ", strip=True),
        )
        status_code = cells[8].get_text(" ", strip=True)
        status = self._path_status_from_dates(apply_start, apply_end)
        capacity_text = cells[7].get_text(" ", strip=True).replace(",", "")
        capacity_match = re.search(r"\d+", capacity_text)
        capacity = int(capacity_match.group()) if capacity_match else 0
        year = cells[1].get_text(" ", strip=True)
        semester = cells[2].get_text(" ", strip=True)
        department = cells[3].get_text(" ", strip=True)
        # The public dialog has no item URL or server-side program ID. Use the
        # stable identifying fields it does expose instead of collapsing all
        # same-title rows into one SQLite record.
        source_key = json.dumps(
            [year, semester, department, title, edu_start, edu_end],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return {
            "title": title,
            "category": self._infer_category(title),
            "status": status,
            "status_code": status_code,
            "source": "ssu_path",
            "source_key": source_key,
            "department": department,
            "program_type": "비교과",
            "description": "",
            "apply_start": apply_start,
            "apply_end": apply_end,
            "edu_start": edu_start,
            "edu_end": edu_end,
            "capacity": capacity,
            "detail_url": "",
        }

    @staticmethod
    def _path_status_from_dates(apply_start: str, apply_end: str, today=None) -> str:
        """Infer a user-facing status; the public list exposes only a numeric code."""
        try:
            start = datetime.strptime(apply_start, "%Y.%m.%d").date()
            end = (
                datetime.strptime(apply_end, "%Y.%m.%d").date()
                if apply_end else start
            )
        except ValueError:
            return "상태 미상"

        today = today or datetime.now(_SEOUL_TIMEZONE).date()
        if today < start:
            return "모집예정"
        if today > end:
            return "모집종료"
        return "모집중"

    @staticmethod
    def _split_date_range(value: str) -> tuple[str, str]:
        match = re.split(r"\s*~\s*|\s+[\-–—]\s+", value, maxsplit=1)
        if len(match) == 2:
            return match[0].strip(), match[1].strip()
        return value.strip(), ""

    async def scrape_and_store(self, db) -> int:
        async with self._scrape_lock:
            logger.info("Starting scrape...")
            programs = await self.scrape_all()
            logger.info(f"Scraped {len(programs)} programs")

            new_count = 0
            for program in programs:
                is_new = await db.upsert_program(program)
                if is_new:
                    new_count += 1

            logger.info(
                "Stored %s new programs and updated %s existing programs",
                new_count, len(programs) - new_count,
            )
            return new_count
