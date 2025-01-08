import asyncio

from dotenv import load_dotenv
from os import environ
import discord
from discord.ext import commands
from datetime import datetime, UTC

from db import SendDB
from utils import SentChecker

load_dotenv()
db = SendDB(f"mongodb+srv://{environ.get('MONGO_USERNAME')}:{environ.get('MONGO_PASSWORD')}@{environ.get('MONGO_ENDPOINT')}")

previous_results = []

async def onSendResults(results: list[int]):
    global previous_results
    timestamp = datetime.now(UTC)
    sends = []
    for i, level_id in enumerate(results):
        if level_id in previous_results: previous_index = previous_results.index(level_id)
        else: previous_index = 99

        print(f"Level ID: {level_id}, Index: {i}, Previous Index: {previous_index}")

        if i < previous_index:
            sends += [{"_id": level_id, "timestamp": timestamp}]

    db.add_sends(sends)

    previous_results = results

checker = SentChecker(onSendResults)

class SendBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='=', intents=discord.Intents.none())
        self.synced = False

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await self.tree.sync()
            self.synced = True
        await self.change_presence(activity=discord.Activity(name='Geometry Dash', type=discord.activity.ActivityType.watching),status=discord.Status.online)
        checker.start(asyncio.get_running_loop())
        print(f"We have logged in as {self.user}.")

    async def close(self):
        checker.stop()
        await super().close()

client = SendBot()
client.run(environ.get("BOT_TOKEN"))