import unittest

from bs4 import BeautifulSoup

from scraper import SsuScraper


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
