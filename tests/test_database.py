import unittest

from database import Database


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_upsert_reports_only_first_insert_as_new(self):
        program = {
            "title": "AI 특강",
            "category": "특강/워크숍",
            "status": "모집중",
            "detail_url": "https://example.com/program/1",
        }

        self.assertTrue(await self.db.upsert_program(program))
        program["description"] = "변경된 설명"
        self.assertFalse(await self.db.upsert_program(program))

        rows = await self.db.get_programs()
        self.assertEqual(rows[0]["description"], "변경된 설명")

    async def test_category_matching_includes_multi_class_programs(self):
        user_id = await self.db.add_user(1234, "tester")
        await self.db.set_user_categories(user_id, ["특강/워크숍"])
        await self.db.upsert_program({
            "title": "데이터 캠프",
            "category": "특강/워크숍",
            "status": "분반모집",
            "detail_url": "https://example.com/program/2",
        })

        matches = await self.db.get_matching_programs(user_id)

        self.assertEqual([row["title"] for row in matches], ["데이터 캠프"])

    async def test_corrected_job_detail_url_updates_legacy_record(self):
        legacy_program = {
            "title": "면접 프로그램",
            "category": None,
            "status": "모집중",
            "detail_url": (
                "https://job.ssu.ac.kr/service/careerProgram/"
                "careerProgramView.do?encSddpbSeq=abc"
            ),
        }
        await self.db.upsert_program(legacy_program)

        corrected_program = {
            **legacy_program,
            "category": "상담/멘토링/코칭",
            "detail_url": (
                "https://job.ssu.ac.kr/service/careerProgram/"
                "careerProgramInfo.do?encSddpbSeq=abc"
            ),
        }
        self.assertFalse(await self.db.upsert_program(corrected_program))

        rows = await self.db.get_programs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "상담/멘토링/코칭")
        self.assertIn("careerProgramInfo.do", rows[0]["detail_url"])

    async def test_keyword_can_be_removed_with_user_id(self):
        user_id = await self.db.add_user(5678, "tester")
        await self.db.add_user_keyword(user_id, "AI")

        await self.db.remove_user_keyword(user_id, "AI")

        self.assertEqual(await self.db.get_user_keywords(user_id), [])
