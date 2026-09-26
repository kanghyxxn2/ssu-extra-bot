import unittest

import discord
from discord.ext import commands

from bot_handlers import setup_bot
from database import Database


class BotCommandTest(unittest.TestCase):
    def test_keyword_commands_are_registered(self):
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        setup_bot(bot, Database(":memory:"), None, None)

        command_names = {command.name for command in bot.tree.get_commands()}

        self.assertIn("키워드추가", command_names)
        self.assertIn("키워드삭제", command_names)
