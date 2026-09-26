import unittest
from datetime import date
from unittest.mock import AsyncMock

import httpx
from bs4 import BeautifulSoup

from scraper import ScrapeSourceError, SsuScraper


class ScraperTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scraper = SsuScraper()

    async def asyncTearDown(self):
        await self.scraper.close()

    def test_job_card_uses_multi_class_status_and_info_detail_url(self):
        html = """
        <div class="cont_box">
          <div class="img_wrap"><span>분반모집</span></div>
          <div class="desc_wrap">
            <div class="label_box"></div>
            <ul class="major_type"><li>진로취업팀</li><li>비교과</li></ul>
            <a class="detailBtn" data-params='{"encSddpbSeq":"abc"}'>
              <span class="tit">면접 멘토링 프로그램</span>
            </a>
            <p class="desc">실전 면접 준비</p>
          </div>
        </div>
        """
        card = BeautifulSoup(html, "lxml").select_one("div.desc_wrap")

        program = self.scraper._extract_job_program(card)

        self.assertEqual(program["status"], "분반모집")
        self.assertTrue(program["detail_url"].endswith("careerProgramInfo.do?encSddpbSeq=abc"))

    def test_path_category_inference_prefers_specific_keywords(self):
        self.assertEqual(
            self.scraper._infer_category("AI 채용설명회", "온라인 행사"),
            "채용설명회/채용상담",
        )
        self.assertEqual(self.scraper._infer_category("알 수 없는 활동"), "기타")

    def test_path_program_receives_inferred_category(self):
        html = """
        <table class="t_list"><tbody><tr>
          <td>1</td><td>2026</td><td>2학기</td><td>교육혁신팀</td>
          <td>데이터 분석 특강</td><td>2026.09.01 ~ 2026.09.30</td>
          <td>2026.10.01 ~ 2026.10.01</td><td>25</td><td>1</td>
        </tr></tbody></table>
        """
        row = BeautifulSoup(html, "lxml").select_one("tr")

        program = self.scraper._extract_path_program(row)

        self.assertEqual(program["category"], "특강/워크숍")
        self.assertEqual(program["department"], "교육혁신팀")
        self.assertEqual(program["apply_start"], "2026.09.01")
        self.assertEqual(program["apply_end"], "2026.09.30")
        self.assertEqual(program["edu_start"], "2026.10.01")
        self.assertEqual(program["edu_end"], "2026.10.01")
        self.assertEqual(program["capacity"], 25)
        self.assertEqual(program["status_code"], "1")
        self.assertEqual(program["status"], "모집중")
        self.assertEqual(program["source"], "ssu_path")
        self.assertTrue(program["source_key"])
        self.assertEqual(program["detail_url"], "")
        self.assertEqual(program["description"], "")

    def test_path_status_is_inferred_from_application_dates(self):
        today = date(2026, 9, 26)
        self.assertEqual(
            self.scraper._path_status_from_dates("2026.10.01", "2026.10.31", today),
            "모집예정",
        )
        self.assertEqual(
            self.scraper._path_status_from_dates("2026.09.01", "2026.09.30", today),
            "모집중",
        )
        self.assertEqual(
            self.scraper._path_status_from_dates("2026.09.26", "", today),
            "모집중",
        )
        self.assertEqual(
            self.scraper._path_status_from_dates("2026.08.01", "2026.08.31", today),
            "모집종료",
        )
        self.assertEqual(
            self.scraper._path_status_from_dates("기간 미정", "", today),
            "상태 미상",
        )

    def test_hyphenated_date_range_is_not_split_inside_date(self):
        self.assertEqual(
            self.scraper._split_date_range("2026-09-01 ~ 2026-09-30"),
            ("2026-09-01", "2026-09-30"),
        )

    async def test_repeated_job_page_fails_instead_of_looping(self):
        repeated_page = [
            {"title": f"프로그램 {index}", "detail_url": f"https://example.com/{index}"}
            for index in range(10)
        ]
        self.scraper._scrape_job_page = AsyncMock(return_value=repeated_page)

        with self.assertRaises(ScrapeSourceError):
            await self.scraper._scrape_job_center()

        self.assertEqual(self.scraper._scrape_job_page.await_count, 2)

    async def test_source_failure_is_not_reported_as_zero_results(self):
        self.scraper._scrape_job_center = AsyncMock(
            side_effect=ScrapeSourceError("network failed"),
        )
        self.scraper._scrape_ssu_path = AsyncMock(
            side_effect=ScrapeSourceError("public list unavailable"),
        )

        with self.assertRaisesRegex(ScrapeSourceError, "All configured"):
            await self.scraper.scrape_all()

        self.assertEqual(
            self.scraper.last_scrape_report["job_center"]["status"], "failed",
        )
        self.assertIn(
            "network failed", self.scraper.last_scrape_report["job_center"]["error"],
        )

    async def test_zero_results_remain_a_successful_source_result(self):
        self.scraper._scrape_job_center = AsyncMock(return_value=[])
        self.scraper._scrape_ssu_path = AsyncMock(return_value=[])

        programs = await self.scraper.scrape_all()

        self.assertEqual(programs, [])
        self.assertEqual(
            self.scraper.last_scrape_report["job_center"],
            {"status": "success", "count": 0},
        )

    def test_path_pagination_uses_public_dialog_markup(self):
        html = """
        <form id="baseForm"><input name="currentPageNo" value="2">
          <ul class="tab_bottom"><li>총게시물 <span>37</span></li>
            <li>페이지 <span>2 / 4</span></li></ul>
          <ul class="page_list"><li><a onclick="global.dialog.page(4)">4</a></li></ul>
        </form>
        """

        self.assertEqual(
            self.scraper._path_pagination(html), ("currentPageNo", 4),
        )

    def test_path_schema_change_is_not_misreported_as_an_empty_result(self):
        html = self._path_list_page(
            1, 1, self._path_row("1", "데이터 분석 특강", "2026.09.01 ~ 2026.09.30"),
        ).replace("<th>운영부서</th>", "<th>담당부서</th>")
        request = httpx.Request("GET", "https://path.ssu.ac.kr/public-list")
        response = httpx.Response(200, request=request, text=html)

        with self.assertRaisesRegex(ScrapeSourceError, "not a public program list"):
            self.scraper._validate_path_content_response(response)

    def test_path_malformed_data_row_fails_instead_of_silently_skipping(self):
        html = """
        <table class="t_list"><thead><tr>
          <th>번호</th><th>년도</th><th>학기</th><th>운영부서</th><th>프로그램명</th>
          <th>신청기간</th><th>교육기간</th><th>모집정원</th><th>진행상태</th>
        </tr></thead><tbody><tr><td>1</td><td>2026</td><td>2학기</td></tr></tbody></table>
        """

        with self.assertRaisesRegex(ScrapeSourceError, "9-column"):
            self.scraper._parse_path_programs(html)

    def test_path_empty_title_fails_instead_of_returning_partial_results(self):
        html = self._path_list_page(
            1, 1, self._path_row("1", "", "2026.09.01 ~ 2026.09.30"),
        )

        with self.assertRaisesRegex(ScrapeSourceError, "empty program title"):
            self.scraper._parse_path_programs(html)

    async def test_path_scrape_uses_public_list_cookie_and_current_page_parameter(self):
        pages = {
            1: self._path_list_page(
                1, 2, self._path_row("1", "데이터 분석 특강", "2026.09.01 ~ 2026.09.30"),
            ),
            2: self._path_list_page(
                2, 2, self._path_row("2", "진로 설계 상담", "2026.09.01 ~ 2026.09.30"),
            ),
        }
        requests = []

        async def handler(request):
            requests.append(request)
            page = int(request.url.params.get("currentPageNo", "1"))
            return httpx.Response(
                200, request=request, text=pages[page],
                headers={"Set-Cookie": "JSESSIONID=public-session; Path=/"} if page == 1 else {},
            )

        self.scraper._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False,
        )

        programs = await self.scraper._scrape_ssu_path()

        self.assertEqual([program["title"] for program in programs], [
            "데이터 분석 특강", "진로 설계 상담",
        ])
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.method == "GET" for request in requests))
        self.assertTrue(all(
            request.url.path == "/ptfol/imng/icmpNsbjtPgm/dialog/nsbjtPgmList.do"
            for request in requests
        ))
        self.assertTrue(all("/comm/login/" not in str(request.url) for request in requests))
        self.assertEqual(requests[1].url.params["currentPageNo"], "2")
        self.assertIn("JSESSIONID=public-session", requests[1].headers.get("cookie", ""))

    async def test_path_redirect_to_login_is_reported_without_following_it(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(
                302, request=request,
                headers={"Location": "https://path.ssu.ac.kr/comm/login/user/login.do"},
            )

        self.scraper._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False,
        )

        with self.assertRaisesRegex(ScrapeSourceError, "redirected unexpectedly"):
            await self.scraper._scrape_ssu_path()
        self.assertEqual(len(requests), 1)

    async def test_path_html_login_page_fails_public_list_validation(self):
        async def handler(request):
            return httpx.Response(
                200, request=request,
                text='<html><form><input name="userId"><input type="password"></form></html>',
            )

        self.scraper._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False,
        )

        with self.assertRaisesRegex(ScrapeSourceError, "not a public program list"):
            await self.scraper._scrape_ssu_path()

    async def test_path_requested_page_must_match_returned_page(self):
        first = self._path_list_page(
            1, 2, self._path_row("1", "데이터 분석 특강", "2026.09.01 ~ 2026.09.30"),
        )
        requests = []

        async def handler(request):
            requests.append(request)
            # Simulate an upstream that ignores currentPageNo=2 and repeats page 1.
            return httpx.Response(200, request=request, text=first)

        self.scraper._http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False,
        )

        with self.assertRaisesRegex(ScrapeSourceError, "did not match the requested page"):
            await self.scraper._scrape_ssu_path()
        self.assertEqual(len(requests), 2)

    @staticmethod
    def _path_row(number, title, apply_period):
        return (
            f"<tr><td>{number}</td><td>2026</td><td>2학기</td>"
            f"<td>교육혁신팀</td><td>{title}</td><td>{apply_period}</td>"
            "<td>2026.10.01 ~ 2026.10.02</td><td>20</td><td>1</td></tr>"
        )

    @staticmethod
    def _path_list_page(current_page, last_page, row):
        links = "".join(
            f'<li><a onclick="global.dialog.page({page})">{page}</a></li>'
            for page in range(1, last_page + 1)
        )
        return f"""
        <form id="baseForm"><input name="currentPageNo" value="{current_page}">
          <table class="t_list"><thead><tr>
            <th>번호</th><th>년도</th><th>학기</th><th>운영부서</th><th>프로그램명</th>
            <th>신청기간</th><th>교육기간</th><th>모집정원</th><th>진행상태</th>
          </tr></thead><tbody>{row}</tbody></table>
          <ul class="tab_bottom"><li>총게시물 <span>{last_page}</span></li>
            <li>페이지 <span>{current_page} / {last_page}</span></li></ul>
          <ul class="page_list">{links}</ul>
        </form>
        """
