import unittest
from unittest.mock import AsyncMock

from notifier import Notifier


class NotifierTest(unittest.IsolatedAsyncioTestCase):
    async def test_user_is_not_notified_before_onboarding(self):
        db = AsyncMock()
        bot = AsyncMock()
        notifier = Notifier(db, bot)
        user = {
            "id": 1,
            "telegram_id": 1234,
            "onboarding_completed_at": None,
        }

        await notifier._notify_user(user)

        db.get_user_categories.assert_not_awaited()
        db.get_new_unnotified_programs.assert_not_awaited()

    def test_keyword_matching_is_case_insensitive(self):
        program = {"title": "생성형 AI 실무 특강", "category": "특강/워크숍"}

        self.assertTrue(Notifier._matches(program, [], ["ai"]))
