import json
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from config import (
    SSU_JOB_LIST_URL,
    SSU_PATH_BASE_URL,
    SSU_PATH_LOGIN_URL,
    SSU_PATH_INDEX_URL,
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


class SsuScraper:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0, headers=_HEADERS, follow_redirects=True,
        )
        self.path_client: httpx.AsyncClient | None = None
        self.path_logged_in = False

    async def close(self):
        await self.client.aclose()
        if self.path_client:
            await self.path_client.aclose()

    async def scrape_all(self) -> list[dict]:
        programs = []

        programs.extend(await self._scrape_job_center())

        if SSU_ID and SSU_PASSWORD:
            path_programs = await self._scrape_ssu_path()
            programs.extend(path_programs)

        return programs

    async def _scrape_job_center(self) -> list[dict]:
        programs = []
        page = 1
        while True:
            page_programs = await self._scrape_job_page(page)
            if not page_programs:
                break
            programs.extend(page_programs)
            if len(page_programs) < _PER_PAGE:
                break
            page += 1
        return programs

    async def _scrape_job_page(self, page: int = 1) -> list[dict]:
        data = {"currentPageNo": str(page), "year": str(datetime.now().year)}
        try:
            response = await self.client.post(SSU_JOB_LIST_URL, data=data)
            response.raise_for_status()
            return self._parse_job_programs(response.text)
        except Exception as e:
            logger.error(f"Error scraping job center page {page}: {e}")
            return []

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
                    detail_url = (
                        "https://job.ssu.ac.kr/service/careerProgram/"
                        f"careerProgramView.do?encSddpbSeq={enc_seq}"
                    )
            except json.JSONDecodeError:
                pass

        return {
            "title": title,
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

    async def _scrape_ssu_path(self) -> list[dict]:
        if not SSU_ID or not SSU_PASSWORD:
            return []

        try:
            if not self.path_logged_in:
                if not self.path_client:
                    self.path_client = httpx.AsyncClient(
                        timeout=30.0, headers=_HEADERS, follow_redirects=True,
                    )
                success = await self._login_ssu_path()
                if not success:
                    logger.error("SSU-PATH login failed")
                    return []
                self.path_logged_in = True

            response = await self.path_client.get(SSU_PATH_INDEX_URL)
            response.raise_for_status()
            return self._parse_path_programs(response.text)
        except Exception as e:
            logger.error(f"Error scraping SSU-PATH: {e}")
            return []

    async def _login_ssu_path(self) -> bool:
        login_data = {
            "userId": SSU_ID,
            "userPwd": SSU_PASSWORD,
            "rtnUrl": "",
        }
        try:
            response = await self.path_client.post(SSU_PATH_LOGIN_URL, data=login_data, follow_redirects=False)
            response.raise_for_status()

            if response.status_code == 302:
                for cookie in self.path_client.cookies.jar:
                    logger.info(f"Cookie set: {cookie.name}")
                return True

            if "<title>로그인" in response.text or "로그인에 실패했습니다" in response.text:
                logger.error("Login failed - still on login page")
                return False

            test_response = await self.path_client.get(SSU_PATH_INDEX_URL, follow_redirects=False)
            if test_response.status_code == 302 and "login" in test_response.headers.get("location", ""):
                logger.error("Index page redirected to login - session not valid")
                return False

            logger.info("SSU-PATH login successful")
            return True
        except Exception as e:
            logger.error(f"SSU-PATH login error: {e}")
            return False

    def _parse_path_programs(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        programs = []
        cards = soup.select("div.program-item, div.card-item, ul.program-list li, tr.program-row")
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
        title_el = card.select_one("a, .title, .program-title, h3, h4, td:first-child a")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title or len(title) < 5:
            return None

        status_el = card.select_one(".status, .badge, .label, [class*='status']")
        status = status_el.get_text(strip=True) if status_el else "모집중"

        desc_el = card.select_one(".desc, .description, p, td:nth-child(2)")
        description = desc_el.get_text(strip=True)[:200] if desc_el else ""

        link_el = card.select_one("a[href*='View'], a[href*='detail']")
        detail_url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                detail_url = f"{SSU_PATH_BASE_URL}{href}"
            elif href.startswith("http"):
                detail_url = href

        text = card.get_text(separator=" ", strip=True)
        dates = re.findall(r"\d{4}\.\d{2}\.\d{2}", text)
        apply_start = dates[0] if dates else ""
        apply_end = dates[1] if len(dates) > 1 else ""

        return {
            "title": title,
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
        match = re.split(r"\s*[~\-]\s*", value, maxsplit=1)
        if len(match) == 2:
            return match[0].strip(), match[1].strip()
        return value.strip(), ""

    async def scrape_and_store(self, db) -> int:
        logger.info("Starting scrape...")
        programs = await self.scrape_all()
        logger.info(f"Scraped {len(programs)} programs")

        new_count = 0
        for program in programs:
            result = await db.upsert_program(program)
            if result:
                new_count += 1

        logger.info(f"Stored {new_count} new/updated programs")
        return new_count