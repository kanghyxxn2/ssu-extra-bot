import discord
from discord import app_commands
from discord.ext import commands

from config import CATEGORIES, CATEGORY_EMOJI, STATUS_EMOJI
from database import Database
from scraper import SsuScraper
from notifier import Notifier


def setup_bot(bot: commands.Bot, db: Database, scraper: SsuScraper, notifier: Notifier):
    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if message.content.strip() in ("!시작", "!start"):
            await _cmd_start(message, db)
        await bot.process_commands(message)

    @bot.tree.command(name="카테고리", description="관심 비교과 분야를 설정합니다")
    async def cmd_categories(interaction: discord.Interaction):
        user_row = await db.add_user(interaction.user.id, interaction.user.name)
        selected = await db.get_user_categories(user_row)
        view = CategorySelectView(db, user_row, selected)
        await interaction.response.send_message(
            "관심 있는 분야를 선택하세요 (여러 개 선택 가능):",
            view=view, ephemeral=True,
        )

    @bot.tree.command(name="설정", description="현재 카테고리/키워드/알림 설정을 확인합니다")
    async def cmd_settings(interaction: discord.Interaction):
        user_row = await db.get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message("먼저 `/카테고리`로 시작해주세요.", ephemeral=True)
            return

        categories = await db.get_user_categories(user_row["id"])
        keywords = await db.get_user_keywords(user_row["id"])
        notif = "🔔 켜짐" if user_row["notifications_enabled"] else "🔕 꺼짐"

        cat_lines = "\n".join(
            f"  {CATEGORY_EMOJI.get(c, '📌')} {c}" for c in categories
        ) if categories else "  (설정 없음)"

        kw_text = ", ".join(f"`{k}`" for k in keywords) if keywords else "(없음)"

        embed = discord.Embed(title="⚙️ 현재 설정", color=0x5865F2)
        embed.add_field(name="📂 관심 카테고리", value=cat_lines, inline=False)
        embed.add_field(name="🔑 키워드", value=kw_text, inline=False)
        embed.add_field(name="🔔 알림", value=notif, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="알림", description="알림을 켜거나 끕니다")
    async def cmd_toggle_notif(interaction: discord.Interaction):
        user_row = await db.get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message("먼저 `/카테고리`로 시작해주세요.", ephemeral=True)
            return
        new_state = await db.toggle_notifications(user_row["id"])
        text = "🔔 알림이 켜졌습니다!" if new_state else "🔕 알림이 꺼졌습니다."
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name="프로그램", description="관심 설정 기반 맞춤 프로그램을 확인합니다")
    async def cmd_programs(interaction: discord.Interaction):
        user_row = await db.get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message("먼저 `/카테고리`로 시작해주세요.", ephemeral=True)
            return

        programs = await db.get_matching_programs(user_row["id"], limit=100)
        if not programs:
            categories = await db.get_user_categories(user_row["id"])
            keywords = await db.get_user_keywords(user_row["id"])
            if not categories and not keywords:
                await interaction.response.send_message(
                    "관심 설정이 없습니다.\n`/카테고리`로 관심 분야를, `/키워드추가`로 키워드를 설정해주세요.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("현재 조건에 맞는 모집중인 프로그램이 없습니다.", ephemeral=True)
            return

        view = ProgramPaginator(programs)
        await interaction.response.send_message(
            embed=view.make_embed(), view=view, ephemeral=True,
        )

    @bot.tree.command(name="새로고침", description="프로그램 목록을 즉시 업데이트합니다")
    async def cmd_refresh(interaction: discord.Interaction):
        await interaction.response.send_message("🔄 프로그램 목록을 업데이트 중...", ephemeral=True)
        count = await scraper.scrape_and_store(db)
        await interaction.edit_original_response(
            content=f"✅ 업데이트 완료! {count}개 프로그램이 업데이트되었습니다.\n`/프로그램`으로 확인하세요."
        )

    @app_commands.command(name="키워드추가", description="관심 키워드를 추가합니다 (예: AI, 데이터)")
    @app_commands.describe(keyword="추가할 키워드")
    async def cmd_keyword_add(interaction: discord.Interaction, keyword: str):
        user_row = await db.add_user(interaction.user.id, interaction.user.name)
        await db.add_user_keyword(user_row, keyword.strip())
        keywords = await db.get_user_keywords(user_row)
        kw_text = ", ".join(f"`{k}`" for k in keywords)
        await interaction.response.send_message(
            f"✅ 키워드 추가: **{keyword}**\n\n현재 키워드: {kw_text}", ephemeral=True,
        )

    @app_commands.command(name="키워드삭제", description="키워드를 삭제합니다")
    @app_commands.describe(keyword="삭제할 키워드")
    async def cmd_keyword_remove(interaction: discord.Interaction, keyword: str):
        user_row = await db.get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message("먼저 `/카테고리`로 시작해주세요.", ephemeral=True)
            return
        await db.remove_user_keyword(user_row, keyword.strip())
        keywords = await db.get_user_keywords(user_row)
        kw_text = ", ".join(f"`{k}`" for k in keywords) if keywords else "(없음)"
        await interaction.response.send_message(
            f"🗑 키워드 삭제: **{keyword}**\n\n현재 키워드: {kw_text}", ephemeral=True,
        )


async def _cmd_start(message: discord.Message, db: Database):
    await db.add_user(message.author.id, message.author.name)
    embed = discord.Embed(
        title="👋 숭실대 비교과 알리미",
        description=(
            "관심 있는 비교과 프로그램만 골라서 알려드려요!\n\n"
            "🎯 `/카테고리` — 관심 분야 설정\n"
            "🔑 `/키워드추가 [단어]` — 키워드 추가\n"
            "🗑 `/키워드삭제 [단어]` — 키워드 삭제\n"
            "📋 `/프로그램` — 맞춤 프로그램 보기\n"
            "🔔 `/알림` — 알림 켜기/끄기\n"
            "⚙️ `/설정` — 현재 설정 확인"
        ),
        color=0x5865F2,
    )
    await message.reply(embed=embed)


class CategorySelectView(discord.ui.View):
    def __init__(self, db: Database, user_id: int, selected: list[str]):
        super().__init__(timeout=120)
        self.db = db
        self.user_id = user_id
        self.add_item(CategorySelect(CATEGORIES, selected))

    @discord.ui.button(label="✅ 완료", style=discord.ButtonStyle.green)
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        categories = await self.db.get_user_categories(self.user_id)
        cat_text = ", ".join(f"{CATEGORY_EMOJI.get(c, '📌')} {c}" for c in categories) if categories else "(없음)"
        await interaction.response.edit_message(
            content=f"✅ 카테고리 설정이 저장되었습니다!\n{cat_text}\n`/프로그램`으로 맞춤 프로그램을 확인하세요.",
            view=None,
        )


class CategorySelect(discord.ui.Select):
    def __init__(self, categories: list[str], selected: list[str]):
        options = [
            discord.SelectOption(
                label=cat,
                value=cat,
                emoji=CATEGORY_EMOJI.get(cat, "📌"),
                default=(cat in selected),
            )
            for cat in categories
        ]
        super().__init__(
            placeholder="관심 분야를 선택하세요...",
            options=options,
            max_values=len(categories),
        )
        self._categories = categories

    async def callback(self, interaction: discord.Interaction):
        view: CategorySelectView = self.view
        await view.db.set_user_categories(view.user_id, self.values)

        self.options = [
            discord.SelectOption(
                label=cat,
                value=cat,
                emoji=CATEGORY_EMOJI.get(cat, "📌"),
                default=(cat in self.values),
            )
            for cat in self._categories
        ]
        await interaction.response.edit_message(view=view)


class ProgramPaginator(discord.ui.View):
    def __init__(self, programs: list):
        super().__init__(timeout=180)
        self.programs = programs
        self.page = 0
        self.per_page = 5

    @discord.ui.button(label="⬅ 이전", style=discord.ButtonStyle.grey)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="➡ 다음", style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (self.page + 1) * self.per_page < len(self.programs):
            self.page += 1
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    def make_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        page_programs = self.programs[start:end]

        total_pages = -(-len(self.programs) // self.per_page)
        embed = discord.Embed(
            title=f"📋 맞춤 비교과 프로그램 ({len(self.programs)}개)",
            color=0x5865F2,
        )
        embed.set_footer(text=f"페이지 {self.page + 1}/{total_pages}")

        for i, p in enumerate(page_programs, start + 1):
            emoji = STATUS_EMOJI.get(p["status"], "📌")
            cat_emoji = CATEGORY_EMOJI.get(p["category"], "📌")
            title = p["title"]
            desc = p["description"][:100] if p["description"] else ""
            apply_end = p["apply_end"] or "미정"
            category = p["category"] or "기타"

            value = f"{emoji} {cat_emoji} {category} | 마감: {apply_end}"
            if desc:
                value += f"\n> {desc}"
            if p["detail_url"]:
                value += f"\n[자세히 보기]({p['detail_url']})"

            embed.add_field(name=f"{i}. {title}", value=value, inline=False)

        return embed
