import unittest
from unittest.mock import AsyncMock

from runtime import run_collection_cycle


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_initial_sync_does_not_send_existing_programs(self):
        db = AsyncMock()
        db.get_state.return_value = None
        scraper = AsyncMock()
        scraper.scrape_and_store.return_value = 10
        notifier = AsyncMock()

        result = await run_collection_cycle(db, scraper, notifier)

        self.assertEqual(result, 10)
        scraper.scrape_and_store.assert_awaited_once_with(db)
        notifier.check_and_notify.assert_not_awaited()
        db.set_state.assert_awaited_once_with("initial_sync_completed", "1")

    async def test_regular_cycle_scrapes_before_notifying(self):
        calls = []
        db = AsyncMock()
        db.get_state.return_value = "1"
        scraper = AsyncMock()
        notifier = AsyncMock()
        scraper.scrape_and_store.side_effect = lambda _: calls.append("scrape")
        notifier.check_and_notify.side_effect = lambda: calls.append("notify")

        await run_collection_cycle(db, scraper, notifier)

        self.assertEqual(calls, ["scrape", "notify"])
