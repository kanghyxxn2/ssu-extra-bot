import logging

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import DISCORD_BOT_TOKEN, DATABASE_PATH, SCRAPE_INTERVAL_HOURS
from database import Database
from scraper import SsuScraper
from bot_handlers import setup_bot
from notifier import Notifier

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database(DATABASE_PATH)
scraper = SsuScraper()
notifier: Notifier = None

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    global notifier
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

    await db.init()
    notifier = Notifier(db, bot)
    setup_bot(bot, db, scraper, notifier)

    logger.info("Running initial scrape...")
    await scraper.scrape_and_store(db)
    logger.info("Initial scrape complete.")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scraper.scrape_and_store, "interval",
        hours=SCRAPE_INTERVAL_HOURS, args=[db],
        id="scraper", name="Scrape SSU programs",
    )
    scheduler.add_job(
        notifier.check_and_notify, "interval",
        hours=SCRAPE_INTERVAL_HOURS,
        id="notifier", name="Send notifications",
    )
    scheduler.start()

    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    logger.info("Bot is ready!")


if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and set your token.")
        exit(1)
    bot.run(DISCORD_BOT_TOKEN)
