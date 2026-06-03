import logging

import discord

from config import CATEGORY_EMOJI, STATUS_EMOJI
from database import Database

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, db: Database, bot: discord.Client):
        self.db = db
        self.bot = bot

    async def check_and_notify(self):
        users = await self.db.get_users_with_notifications()
        logger.info(f"Checking notifications for {len(users)} users")

        for user in users:
            try:
                await self._notify_user(user)
            except Exception as e:
                logger.error(f"Failed to notify user {user['telegram_id']}: {e}")

    async def _notify_user(self, user):
        user_id = user["id"]
        discord_id = user["telegram_id"]

        categories = await self.db.get_user_categories(user_id)
        keywords = await self.db.get_user_keywords(user_id)
        if not categories and not keywords:
            return

        programs = await self.db.get_new_unnotified_programs(user_id)
        matching = [p for p in programs if self._matches(p, categories, keywords)]

        for program in matching[:5]:
            embed = self._make_embed(program)
            try:
                discord_user = self.bot.get_user(discord_id)
                if not discord_user:
                    discord_user = await self.bot.fetch_user(discord_id)
                if discord_user:
                    await discord_user.send(embed=embed)
                    await self.db.log_notification(user_id, program["id"])
            except discord.Forbidden:
                logger.warning(f"Cannot DM user {discord_id} (DMs disabled)")
            except Exception as e:
                logger.error(f"Failed to send DM to {discord_id}: {e}")

    @staticmethod
    def _matches(program, categories: list[str], keywords: list[str]) -> bool:
        cat_match = program["category"] in categories if categories else False
        kw_match = any(kw in program["title"] for kw in keywords) if keywords else False
        return cat_match or kw_match

    @staticmethod
    def _make_embed(program) -> discord.Embed:
        emoji = STATUS_EMOJI.get(program["status"], "🟢")
        cat_emoji = CATEGORY_EMOJI.get(program["category"], "📌")
        title = program["title"]
        desc = program["description"][:200] if program["description"] else ""
        apply_end = program["apply_end"] or "미정"
        category = program["category"] or "기타"

        embed = discord.Embed(
            title=f"{emoji} {title}",
            description=desc,
            color=0x57F287,
        )
        embed.add_field(name="분야", value=f"{cat_emoji} {category}", inline=True)
        embed.add_field(name="신청 마감", value=apply_end, inline=True)
        if program["capacity"]:
            embed.add_field(name="모집정원", value=f"{program['capacity']}명", inline=True)
        if program["detail_url"]:
            embed.add_field(name="링크", value=f"[자세히 보기]({program['detail_url']})", inline=False)
        embed.set_footer(text="숭실대 비교과 알리미")

        return embed
