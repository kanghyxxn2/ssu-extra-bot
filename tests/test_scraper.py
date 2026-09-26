import unittest
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
        <tr>
          <td><a href="/program/1">데이터 분석 특강</a></td>
          <td>모집중</td>
          <td><div>실무 교육</div></td>
        </tr>
        """
        row = BeautifulSoup(html, "lxml").select_one("tr")

        program = self.scraper._extract_path_program(row)

        self.assertEqual(program["category"], "특강/워크숍")

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

        programs = await self.scraper.scrape_all()

        self.assertEqual(programs, [])
        self.assertEqual(
            self.scraper.last_scrape_report["job_center"],
            {"status": "success", "count": 0},
        )

    def test_path_pagination_detects_parameter_and_last_page(self):
        html = """
        <input name="pageIndex" value="1">
        <div class="pagination">
          <a onclick="fn_egov_link_page(1)">1</a>
          <a onclick="fn_egov_link_page(2)">2</a>
          <a onclick="fn_egov_link_page(3)">3</a>
        </div>
        """

        self.assertEqual(
            self.scraper._path_pagination(html), ("pageIndex", 3),
        )

    def test_path_login_page_is_rejected_as_program_content(self):
        request = httpx.Request("GET", "https://path.ssu.ac.kr/programs")
        response = httpx.Response(
            200,
            request=request,
            text='<input name="userId"><input name="userPwd" type="password">',
        )

        with self.assertRaises(ScrapeSourceError):
            self.scraper._validate_path_content_response(response)
