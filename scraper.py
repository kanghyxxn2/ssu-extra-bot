import asyncio
import json
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from config import (
    SSU_JOB_LIST_URL,
    SSU_JOB_DETAIL_URL,
    SSU_JOB_CATEGORY_CODES,
    SSU_PATH_BASE_URL,
    SSU_PATH_LOGIN_URL,
    SSU_PATH_INDEX_URL,
    SSU_PATH_LIST_URL,
    SSU_ID,
    SSU_PASSWORD,
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
_PATH_PAGINATION_SELECTORS = (
    ".pagination", ".paging", ".paginate", ".paginationSet",
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

        if SSU_ID and SSU_PASSWORD:
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
        else:
            self.last_scrape_report["ssu_path"] = {
                "status": "skipped", "count": 0,
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
            "operYySh": str(datetime.now().year),
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
        if not SSU_ID or not SSU_PASSWORD:
            return []

        if not self._http_client:
            self._http_client = httpx.AsyncClient(
                timeout=30.0, headers=_HEADERS, follow_redirects=False,
            )

        logger.info("Getting SSU-PATH login page for cookies...")
        login_page = await self._http_client.get(SSU_PATH_LOGIN_URL)
        login_page.raise_for_status()

        soup = BeautifulSoup(login_page.text, "lxml")
        csrf_input = soup.select_one("input[name='CSRF_TOKEN']")
        csrf_token = csrf_input.get("value", "") if csrf_input else ""

        login_data = {
            "userId": SSU_ID,
            "userPwd": SSU_PASSWORD,
            "rtnUrl": SSU_PATH_INDEX_URL,
        }
        if csrf_token:
            login_data["CSRF_TOKEN"] = csrf_token

        logger.info("Posting SSU-PATH login...")
        login_response = await self._http_client.post(
            SSU_PATH_LOGIN_URL,
            data=login_data,
            follow_redirects=False,
        )
        self._validate_path_login_response(login_response)

        response = await self._http_client.get(SSU_PATH_LIST_URL)
        self._validate_path_content_response(response)
        programs = self._parse_path_programs(response.text)

        page_parameter, last_page = self._path_pagination(response.text)
        for page in range(2, last_page + 1):
            page_response = await self._http_client.get(
                SSU_PATH_LIST_URL, params={page_parameter: str(page)},
            )
            self._validate_path_content_response(page_response)
            page_programs = self._parse_path_programs(page_response.text)
            if not page_programs:
                raise ScrapeSourceError(
                    f"SSU-PATH page {page} was empty before page {last_page}"
                )
            programs.extend(page_programs)

        logger.info("SSU-PATH list response: %s", response.status_code)
        return programs

    @staticmethod
    def _looks_like_path_login(html: str) -> bool:
        soup = BeautifulSoup(html, "lxml")
        return bool(
            soup.select_one("input[type='password']")
            and soup.select_one("input[name='userId'], input[name='userPwd']")
        )

    def _validate_path_login_response(self, response: httpx.Response):
        if "로그인에 실패했습니다" in response.text:
            raise ScrapeSourceError("SSU-PATH rejected the configured credentials")
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if "login" in location.lower():
                raise ScrapeSourceError(
                    f"SSU-PATH redirected back to login: {location}"
                )
            return
        response.raise_for_status()
        if self._looks_like_path_login(response.text):
            raise ScrapeSourceError("SSU-PATH returned the login page after login")

    def _validate_path_content_response(self, response: httpx.Response):
        if response.is_redirect:
            location = response.headers.get("location", "")
            raise ScrapeSourceError(
                f"SSU-PATH program list redirected unexpectedly: {location}"
            )
        response.raise_for_status()
        if self._looks_like_path_login(response.text):
            raise ScrapeSourceError(
                "SSU-PATH program list returned a login page"
            )

    @staticmethod
    def _path_pagination(html: str) -> tuple[str | None, int]:
        soup = BeautifulSoup(html, "lxml")
        container = next(
            (soup.select_one(selector) for selector in _PATH_PAGINATION_SELECTORS
             if soup.select_one(selector)),
            None,
        )
        if not container:
            return None, 1

        pages = [
            int(text) for text in container.stripped_strings
            if text.isdigit() and int(text) > 0
        ]
        last_page = max(pages, default=1)
        if last_page == 1:
            return None, 1

        markup = str(container)
        parameter_names = (
            "paginationInfo.currentPageNo", "currentPageNo", "pageIndex", "pageNo",
        )
        for parameter in parameter_names:
            if parameter in markup or soup.select_one(f"input[name='{parameter}']"):
                return parameter, last_page

        if "fn_egov_link_page" in markup:
            return "pageIndex", last_page
        raise ScrapeSourceError(
            "SSU-PATH pagination exists but its page parameter is unknown"
        )

    def _parse_path_programs(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        programs = []

        cards = soup.select("tr")
        for card in cards:
            try:
                program = self._extract_path_program(card)
                if program and len(program.get("title", "")) > 5:
                    program["source"] = "ssu_path"
                    programs.append(program)
            except Exception as e:
                logger.warning(f"Failed to parse path card: {e}")
        return programs

    def _extract_path_program(self, card: Tag) -> dict | None:
        cells = card.select("td")
        if len(cells) < 2:
            return None

        first_cell = cells[0]
        title_el = first_cell.select_one("a")
        title = title_el.get_text(strip=True) if title_el else first_cell.get_text(strip=True)

        if not title or len(title) < 5:
            return None

        status = "모집중"
        if len(cells) > 1:
            status_cell = cells[1]
            status = status_cell.get_text(strip=True) or "모집중"

        description = ""
        if len(cells) > 2:
            desc_el = cells[2].select_one("div")
            description = desc_el.get_text(strip=True)[:200] if desc_el else ""

        detail_url = ""
        if title_el:
            href = title_el.get("href", "")
            if href.startswith("/"):
                detail_url = f"{SSU_PATH_BASE_URL}{href}"
            elif href.startswith("http"):
                detail_url = href

        apply_start = ""
        apply_end = ""
        text = card.get_text(separator=" ", strip=True)
        dates = re.findall(r"\d{4}\.\d{2}\.\d{2}", text)
        if len(dates) >= 1:
            apply_start = dates[0]
        if len(dates) >= 2:
            apply_end = dates[1]

        return {
            "title": title,
            "category": self._infer_category(title, description),
            "status": status,
            "department": "숭실대학교",
            "program_type": "비교과",
            "description": description,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "detail_url": detail_url,
        }

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
