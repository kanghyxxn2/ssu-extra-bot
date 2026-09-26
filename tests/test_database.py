import sqlite3
import tempfile
import unittest
from pathlib import Path

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

    async def test_notifications_only_include_programs_after_onboarding(self):
        await self.db.upsert_program({
            "title": "기존 특강",
            "category": "특강/워크숍",
            "status": "모집중",
            "detail_url": "https://example.com/program/old",
        })
        user_id = await self.db.add_user(9999, "new-user")
        await self.db.set_user_categories(user_id, ["특강/워크숍"])
        await self.db.complete_onboarding(user_id)
        user = await self.db.get_user(9999)
        await self.db.upsert_program({
            "title": "신규 특강",
            "category": "특강/워크숍",
            "status": "모집중",
            "detail_url": "https://example.com/program/new",
        })

        programs = await self.db.get_new_unnotified_programs(
            user_id, user["onboarding_completed_at"],
        )

        self.assertEqual([row["title"] for row in programs], ["신규 특강"])

    async def test_complete_onboarding_keeps_original_timestamp(self):
        user_id = await self.db.add_user(1010, "tester")
        await self.db.complete_onboarding(user_id)
        first = (await self.db.get_user(1010))["onboarding_completed_at"]

        await self.db.complete_onboarding(user_id)
        second = (await self.db.get_user(1010))["onboarding_completed_at"]

        self.assertEqual(first, second)


class DatabaseMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_users_are_baselined_during_onboarding_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """CREATE TABLE users (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       telegram_id INTEGER UNIQUE NOT NULL,
                       username TEXT,
                       notifications_enabled INTEGER DEFAULT 1,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            connection.execute(
                """INSERT INTO users
                       (telegram_id, username, created_at, updated_at)
                   VALUES (1, 'legacy-user', '2026-01-01', '2026-01-01')"""
            )
            connection.commit()
            connection.close()

            db = Database(str(db_path))
            await db.init()
            try:
                user = await db.get_user(1)
                self.assertIsNotNone(user["onboarding_completed_at"])
            finally:
                await db.close()
