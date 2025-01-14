import asyncio

from dotenv import load_dotenv
from os import environ
import discord, os, json
from discord.ext import commands
from discord import app_commands
from datetime import datetime, UTC

from db import SendDB
from utils import SentChecker

load_dotenv()
db = SendDB(f"mongodb+srv://{environ.get('MONGO_USERNAME')}:{environ.get('MONGO_PASSWORD')}@{environ.get('MONGO_ENDPOINT')}")

def load_previous_data():
    if os.path.exists("previous_data.json"):
        with open("previous_data.json", "r") as file:
            data = json.load(file)
            return data.get("previous_levels", [])
    return []

def save_previous_data(levels):
    data = {
        "previous_levels": levels
    }
    with open("previous_data.json", "w") as file:
        json.dump(data, file)

previous_levels = load_previous_data()

async def onSendResults(levels: list[dict], creators: list[dict], rated_levels: list[int]):
    global previous_levels

    timestamp = datetime.now(UTC)

    creatorMap = {creator["_id"]: creator["name"] for creator in creators}
    accountMap = {creator["_id"]: creator["accountID"] for creator in creators}
    level_ids = [level["_id"] for level in levels]

    sends = []
    info = []
    webhookInfo = []

    # Check which levels from previous_levels are no longer in the current list
    disappeared_levels = [level_id for level_id in previous_levels if level_id not in level_ids]

    ignoreIndex = len(level_ids)
    for id in disappeared_levels:
        if id in rated_levels:
            previous_levels.remove(id)
            ignoreIndex -= 1

    for i, level_id in enumerate(level_ids):
        if i >= ignoreIndex:
            break

        if level_id in previous_levels:
            previous_index = previous_levels.index(level_id)
        else:
            previous_index = 99

        if i < previous_index:
            creator_id = levels[i]["creatorID"]
            creator_name = creatorMap[creator_id]
            if not creator_name:
                creator_name = "Unknown"

            sends += [{"levelID": level_id, "timestamp": timestamp}]
            info += [{"_id": level_id, "name": levels[i]["name"], "creator": levels[i]["creatorID"]}]
            webhookInfo += [{
                "_id": level_id,
                "name": levels[i]["name"],
                "creator": creator_name,
                "creatorID": accountMap[levels[i]["creatorID"]],
                "sends": 1
            }]

    previous_levels = level_ids
    save_previous_data(previous_levels)

    db.add_sends(sends)
    db.add_info(info)

    if webhookInfo:
        print("New sent levels!")
        db.add_creators(creators)
        checkIds = [level["_id"] for level in webhookInfo]
        sendMap = db.get_sends(checkIds)
        for sendID, sendCount in sendMap.items():
            for level in webhookInfo:
                if level["_id"] == sendID:
                    level["sends"] = sendCount["count"]

        await sendMessage(webhookInfo, timestamp)

checker = SentChecker(onSendResults)

class SendBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='=', intents=discord.Intents.none())
        self.sendChannel: discord.TextChannel = None
        self.synced = False

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await self.tree.sync()
            self.synced = True
        await self.change_presence(activity=discord.Activity(name='Sent Levels', type=discord.ActivityType.watching), status=discord.Status.online)
        self.sendChannel = (self.get_channel(int(environ.get('SEND_CHANNEL_ID'))) or await self.fetch_channel(int(environ.get('SEND_CHANNEL_ID'))))
        if self.sendChannel is None or not self.sendChannel.is_news():
            print("Send channel not found or not news.")

        checker.start(asyncio.get_running_loop())
        print(f"We have logged in as {self.user}.")

    async def close(self):
        checker.stop()
        await super().close()

client = SendBot()


async def sendMessage(info: list[dict], timestamp: datetime):
    embeds = []
    for level in info:
        embed = discord.Embed(
            title=level["name"],
            description=f"By **{level['creator']}** ({level['creatorID']})\nTotal Sends: **{level['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level['_id']}) (`{level['_id']}`)",
            color=0x00ff00
        )
        embed.set_author(name=level["creator"], url=f"https://gdbrowser.com/u/{level['creatorID']}", icon_url="https://gdbrowser.com/assets/cp.png")
        embed.timestamp = timestamp

        embeds.append(embed)

    num = len(info)
    s = "s" if num != 1 else ""
    message = await client.sendChannel.send(content=f"**{num}** level{s} sent.\nCheck time: <t:{int(timestamp.timestamp())}:F> (<t:{int(timestamp.timestamp())}:R>)", embeds=embeds)
    await message.publish()

@client.tree.command(name="subscribe", description="Subscribe this channel to level send notifications.")
@commands.has_permissions(manage_channels=True)
async def subscribe(interaction: discord.Interaction):
    if client.sendChannel is None:
        await interaction.response.send_message("❌ Internal error.", ephemeral=True)
        return

    try:
        await client.sendChannel.follow(destination=interaction.channel, reason=f"{interaction.user.id} subscribed to level send notifications.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions", ephemeral=True)
        return

    await interaction.response.send_message("✅ Subscribed to level send notifications.",ephemeral=True)

@client.tree.command(name="check-level", description="Check a level's sends.")
@app_commands.describe(level_id="The level's ID.")
async def check_level(interaction: discord.Interaction, level_id: int):
    sendData = db.get_sends([level_id])
    if level_id not in sendData:
        await interaction.response.send_message(f"❌ Level `{level_id}` has no sends.", ephemeral=True)
        return
    sendCount = sendData[level_id]["count"]
    lastSend: datetime = sendData[level_id]["latest_timestamp"]

    infoData = db.get_info([level_id])
    if level_id not in infoData:
        levelData = {
            "_id": level_id,
            "name": level_id
        }
        creatorString = ""

    else:
        levelData = infoData[level_id]
        creatorData = db.get_creators([levelData["creator"]])

        if levelData["creator"] not in creatorData:
            levelData["creatorName"] = "Unknown"
            creatorString = ""

        else:
            levelData["creatorName"] = creatorData[levelData["creator"]]["name"]
            levelData["accountID"] = creatorData[levelData["creator"]]["accountID"]
            creatorString = f"By **{levelData['creatorName']}** ({levelData['creator']})\n"

    embed = discord.Embed(
        title=f"{levelData['name']}",
        description=f"{creatorString}Total Sends: **{sendCount}**\nLast Sent: <t:{int(lastSend.timestamp())}:F> (<t:{int(lastSend.timestamp())}:R>)\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_id}) (`{level_id}`)",
        color=0x00ff00
    )
    if creatorString:
        embed.set_author(name=levelData["creatorName"], url=f"https://gdbrowser.com/u/{levelData['accountID']}", icon_url="https://gdbrowser.com/assets/cp.png")

    await interaction.response.send_message(embed=embed)


client.run(environ.get("BOT_TOKEN"))