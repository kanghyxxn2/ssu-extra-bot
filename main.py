import asyncio
import logging

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext import commands

from bot_handlers import setup_bot
from config import DATABASE_PATH, DISCORD_BOT_TOKEN, SCRAPE_INTERVAL_HOURS
from database import Database
from notifier import Notifier
from runtime import run_collection_cycle
from scraper import SsuScraper

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database(DATABASE_PATH)
scraper = SsuScraper()
scheduler = AsyncIOScheduler()
cycle_lock = asyncio.Lock()


class SsuExtraBot(commands.Bot):
    async def setup_hook(self):
        await db.init()
        try:
            synced = await self.tree.sync()
            logger.info("Synced %s slash commands", len(synced))
        except Exception:
            logger.exception("Failed to sync slash commands")

        await run_scheduled_cycle()
        scheduler.add_job(
            run_scheduled_cycle,
            "interval",
            hours=SCRAPE_INTERVAL_HOURS,
            id="collection-cycle",
            name="Scrape SSU programs and send notifications",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()

    async def close(self):
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await scraper.close()
        await db.close()
        await super().close()


intents = discord.Intents.default()
intents.message_content = True
bot = SsuExtraBot(command_prefix="!", intents=intents)
notifier = Notifier(db, bot)
setup_bot(bot, db, scraper, notifier)


async def run_scheduled_cycle():
    if cycle_lock.locked():
        logger.warning("Skipping collection cycle because the previous cycle is running")
        return

    async with cycle_lock:
        await run_collection_cycle(db, scraper, notifier)


@bot.event
async def on_ready():
    logger.info("Bot is ready as %s (ID: %s)", bot.user, bot.user.id)


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise SystemExit(
            "ERROR: DISCORD_BOT_TOKEN is not set. "
            "Copy .env.example to .env and set your token."
        )
    bot.run(DISCORD_BOT_TOKEN)
