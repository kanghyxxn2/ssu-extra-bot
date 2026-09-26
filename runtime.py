import logging

from database import Database
from notifier import Notifier
from scraper import SsuScraper

logger = logging.getLogger(__name__)

_INITIAL_SYNC_KEY = "initial_sync_completed"


async def run_collection_cycle(
    db: Database, scraper: SsuScraper, notifier: Notifier,
) -> int:
    initial_sync_completed = await db.get_state(_INITIAL_SYNC_KEY)
    new_count = await scraper.scrape_and_store(db)

    if initial_sync_completed:
        await notifier.check_and_notify()
    else:
        await db.set_state(_INITIAL_SYNC_KEY, "1")
        logger.info("Initial data sync completed; notifications start next cycle")

    return new_count
