import asyncio

from dotenv import load_dotenv
from os import environ
import discord, os, json, re
from discord.ext import commands
from discord.ui import Button, View
from discord import app_commands
from datetime import datetime, UTC
from math import ceil
from enum import Enum
from typing import Literal

from db import SendDB
from utils import SentChecker

load_dotenv()
db = SendDB(f"mongodb+srv://{environ.get('MONGO_USERNAME')}:{environ.get('MONGO_PASSWORD')}@{environ.get('MONGO_ENDPOINT')}")

OLDEST_LEVEL = int(environ.get("OLDEST_LEVEL"))

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
    if not levels or not creators or not rated_levels:
        print("No data received.")
        return
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
                    await notify_followers(level, timestamp)

        await sendMessage(webhookInfo, timestamp)

async def sendBanNotification():
    await client.sendChannel.send("❌ **Bot was IP Banned!**")

checker = SentChecker(onSendResults, sendBanNotification)

class SendBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='=', intents=discord.Intents.none())
        self.sendChannel: discord.TextChannel = None
        self.synced = False

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await self.add_cog(FollowCommands(client))
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

class LeaderboardType(Enum):
    CREATORS = "Creators"
    LEVELS = "Levels"

class PageModal(discord.ui.Modal, title="Go to Page"):
    def __init__(self, max_pages: int):
        super().__init__()
        self.page_number = discord.ui.TextInput(
            label="Page Number",
            placeholder=f"Enter a page number (1-{max_pages})",
            min_length=1,
            max_length=len(str(max_pages))
        )
        self.add_item(self.page_number)
        self.max_pages = max_pages

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_number.value)
            if 1 <= page <= self.max_pages:
                self.page = page - 1  # Convert to 0-based index
                await interaction.response.defer()
            else:
                await interaction.response.send_message(f"Please enter a number between 1 and {self.max_pages}", ephemeral=True)
                self.page = None
        except ValueError:
            await interaction.response.send_message("Please enter a valid number", ephemeral=True)
            self.page = None

class TypeSelect(discord.ui.Select):
    def __init__(self, parent_view: "LeaderboardView"):
        self._view = parent_view  # Store the view reference directly
        options = [
            discord.SelectOption(label="Creator Leaderboard", value="CREATORS", description="Show sends by creator"),
            discord.SelectOption(label="Level Leaderboard", value="LEVELS", description="Show sends by level")
        ]
        super().__init__(placeholder="Select leaderboard type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self._view.type = LeaderboardType[self.values[0]]
        self._view.current_page = 0
        self._view.searched_id = None
        self._view.update_buttons()
        await interaction.response.edit_message(embed=await self._view.get_embed(), view=self._view)

class SearchModal(discord.ui.Modal):
    def __init__(self, search_type: LeaderboardType):
        super().__init__(
            title=f"Search {'Creator' if search_type == LeaderboardType.CREATORS else 'Level'} ID"
        )
        self.search_id = discord.ui.TextInput(
            label=f"{'User' if search_type == LeaderboardType.CREATORS else 'Level'} ID {'(NOT Player ID)' if search_type == LeaderboardType.CREATORS else ''}",
            placeholder="Enter ID...",
            min_length=1,
            max_length=20
        )
        self.add_item(self.search_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.id = int(self.search_id.value)
            await interaction.response.defer()
        except ValueError:
            await interaction.response.send_message("Please enter a valid number", ephemeral=True)
            self.id = None

class LeaderboardView(View):
    def __init__(self, db: SendDB, owner_id: int, page_size: int = 10):
        super().__init__(timeout=180)
        self.db = db
        self.owner_id = owner_id
        self.page_size = page_size
        self.current_page = 0
        self.total_count = 0
        self.type = LeaderboardType.CREATORS
        self.searched_id = None

        # Add type selector
        self.type_select = TypeSelect(self)
        self.add_item(self.type_select)

    def get_pipeline_for_type(self, skip: int, limit: int) -> list[dict]:
        if self.type == LeaderboardType.CREATORS:
            return [
                {"$group": {
                    "_id": "$levelID",
                    "count": {"$sum": 1}
                }},
                {"$lookup": {
                    "from": "info",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "level_info"
                }},
                {"$unwind": "$level_info"},
                {"$group": {
                    "_id": "$level_info.creator",
                    "sends": {"$sum": "$count"},
                    "level_count": {"$addToSet": "$_id"}
                }},
                {"$facet": {
                    "total": [{"$count": "count"}],
                    "data": [
                        {"$lookup": {
                            "from": "creators",
                            "localField": "_id",
                            "foreignField": "_id",
                            "as": "creator_info"
                        }},
                        {"$unwind": "$creator_info"},
                        {"$project": {
                            "name": "$creator_info.name",
                            "accountID": "$creator_info._id",
                            "sends": 1,
                            "level_count": {"$size": "$level_count"}
                        }},
                        {"$sort": {
                            "sends": -1,
                            "_id": 1
                        }},
                        {"$skip": skip},
                        {"$limit": limit}
                    ]
                }}
            ]
        else:
            return [
                {"$group": {
                    "_id": "$levelID",
                    "sends": {"$sum": 1}
                }},
                {"$facet": {
                    "total": [{"$count": "count"}],
                    "data": [
                        {"$lookup": {
                            "from": "info",
                            "localField": "_id",
                            "foreignField": "_id",
                            "as": "level_info"
                        }},
                        {"$unwind": "$level_info"},
                        {"$lookup": {
                            "from": "creators",
                            "localField": "level_info.creator",
                            "foreignField": "_id",
                            "as": "creator_info"
                        }},
                        {"$unwind": "$creator_info"},
                        {"$project": {
                            "name": "$level_info.name",
                            "creator": "$creator_info.name",
                            "creatorID": "$creator_info._id",
                            "levelID": "$_id",
                            "sends": 1
                        }},
                        {"$sort": {
                            "sends": -1,
                            "_id": 1
                        }},
                        {"$skip": skip},
                        {"$limit": limit}
                    ]
                }}
            ]

    async def get_page_data(self) -> tuple[list[dict], int]:
        skip = self.current_page * self.page_size
        pipeline = self.get_pipeline_for_type(skip, self.page_size)

        result = db.raw_pipeline("sends", pipeline)
        if not result or not result[0]["total"]:
            return [], 0

        return result[0]["data"], result[0]["total"][0]["count"]

    async def find_page_for_id(self, search_id: int) -> int:
        """Find the page number containing the given ID"""
        if self.type == LeaderboardType.CREATORS:
            pipeline = [
                {"$group": {
                    "_id": "$levelID",
                    "count": {"$sum": 1}
                }},
                {"$lookup": {
                    "from": "info",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "level_info"
                }},
                {"$unwind": "$level_info"},
                {"$group": {
                    "_id": "$level_info.creator",
                    "sends": {"$sum": "$count"}
                }},
                {"$lookup": {
                    "from": "creators",
                    "localField": "_id",
                    "foreignField": "_id",
                    "as": "creator_info"
                }},
                {"$unwind": "$creator_info"},
                {"$sort": {
                    "sends": -1,
                    "_id": 1
                }},
                {"$group": {
                    "_id": None,
                    "position": {
                        "$push": "$_id"
                    }
                }},
                {"$project": {
                    "index": {
                        "$indexOfArray": ["$position", search_id]
                    }
                }}
            ]
        else:
            pipeline = [
                {"$group": {
                    "_id": "$levelID",
                    "sends": {"$sum": 1}
                }},
                {"$sort": {
                    "sends": -1,
                    "_id": 1
                }},
                {"$group": {
                    "_id": None,
                    "position": {
                        "$push": "$_id"
                    }
                }},
                {"$project": {
                    "index": {
                        "$indexOfArray": ["$position", search_id]
                    }
                }}
            ]

        result = db.raw_pipeline("sends", pipeline)
        if not result or result[0]["index"] == -1:
            return None

        position = result[0]["index"]
        return position // self.page_size

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_pages - 1

    async def get_embed(self) -> discord.Embed:
        page_data, self.total_count = await self.get_page_data()
        self.max_pages = ceil(self.total_count / self.page_size)

        embed = discord.Embed(
            title=f"{self.type.value} Leaderboard",
            description=f"Most {'level sends by creator' if self.type == LeaderboardType.CREATORS else 'sent levels'}",
            color=0x00ff00
        )

        start_idx = self.current_page * self.page_size

        for idx, entry in enumerate(page_data, start=start_idx + 1):
            medal = "⭐" if self.searched_id and self.searched_id == entry["accountID" if self.type == LeaderboardType.CREATORS else "levelID"] else ""

            if idx == 1:
                medal += "🥇"
            elif idx == 2:
                medal += "🥈"
            elif idx == 3:
                medal += "🥉"

            if self.type == LeaderboardType.CREATORS:
                embed.add_field(
                    name=f"{medal}#{idx}. {entry['name']} ({entry['accountID']})",
                    value=f"Total Sends: **{entry['sends']}** over `{entry['level_count']}` level{'s' if entry['level_count'] != 1 else ''}",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"{medal}#{idx}. {entry['name']} ({entry['levelID']})",
                    value=f"By **{entry['creator']}** ({entry['creatorID']})\nTotal Sends: **{entry['sends']}**",
                    inline=False
                )

        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages} • Total {'Players' if self.type == LeaderboardType.CREATORS else 'Levels'}: {self.total_count}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Top", style=discord.ButtonStyle.primary, emoji="⬆️")
    async def top_button(self, interaction: discord.Interaction, button: Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, emoji="⬅️")
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_page = min(self.max_pages - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def search_button(self, interaction: discord.Interaction, button: Button):
        modal = SearchModal(self.type)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if modal.id is not None:
            page = await self.find_page_for_id(modal.id)
            if page is not None:
                self.current_page = page
                self.searched_id = modal.id
                self.update_buttons()
                await interaction.edit_original_response(embed=await self.get_embed(), view=self)
            else:
                entity_type = "creator" if self.type == LeaderboardType.CREATORS else "level"
                await interaction.followup.send(f"Could not find {entity_type} with ID {modal.id}", ephemeral=True)

    @discord.ui.button(label="Go to...", style=discord.ButtonStyle.secondary)
    async def goto_button(self, interaction: discord.Interaction, button: Button):
        modal = PageModal(self.max_pages)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if modal.page is not None:
            self.current_page = modal.page
            self.update_buttons()
            await interaction.edit_original_response(embed=await self.get_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except:
            pass

    async def callback(self, interaction: discord.Interaction):
        self.type = LeaderboardType[self.type_select.values[0]]
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=await self.get_embed(), view=self)

class FollowCommands(commands.GroupCog, name="follow"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @staticmethod
    def extract_id(creator_string):
        if match := re.search(r'\((\d+)\)', creator_string):
            return int(match.group(1))

        if match := re.search(r'(\d+)$', creator_string):
            return int(match.group(1))

        if creator_string.isdigit():
            return int(creator_string)

        return ''

    async def creator_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current or '(' in current:
            return []
        creators = db.search_creators(current)
        return [
                   app_commands.Choice(name=f"{creator['name']} ({creator['_id']})", value=str(creator['_id']))
                   for creator in creators
               ][:25]

    @app_commands.command(name="creator", description="Follow a creator to get DM notifications when their levels are sent")
    @app_commands.autocomplete(creator=creator_autocomplete)
    async def follow_creator(self, interaction: discord.Interaction, creator: str):
        creator_id = self.extract_id(creator)

        # Verify creator exists
        creators = db.get_creators([creator_id])
        if creator_id not in creators:
            if not creator.isdigit():
                # Try to find creator by name
                async def callback(player_id: int, account_id: int):
                    db.add_creators([{"_id": player_id, "name": creator, "accountID": account_id}])
                    db.add_follow(interaction.user.id, "creator", player_id)
                    await interaction.followup.send(f"✅ Now following **{creator}** with ID: `{player_id}`", ephemeral=True)

                await interaction.response.defer(ephemeral=True)
                checker.queue_check(creator, callback)
                return

            await interaction.response.send_message(f"❌ Creator `{creator}` not found", ephemeral=True)
            return

        db.add_follow(interaction.user.id, "creator", creator_id)
        await interaction.response.send_message(f"✅ Now following **{creators[creator_id]['name']}**", ephemeral=True)

    @app_commands.command(name="level", description="Follow a level to get DM notifications when it is sent")
    async def follow_level(self, interaction: discord.Interaction, level_id: int):
        info = db.get_info([level_id])
        if level_id in info:
            level_name = f"**{info[level_id]['name']}**"
        else:
            level_name = f"`{level_id}`"

        db.add_follow(interaction.user.id, "level", level_id)
        await interaction.response.send_message(f"✅ Now following level {level_name}", ephemeral=True)

    @app_commands.command(name="list", description="List all your followed creators and levels")
    async def list_follows(self, interaction: discord.Interaction):
        follows = db.get_follows(interaction.user.id)
        if not follows:
            await interaction.response.send_message("You're not following any creators or levels", ephemeral=True)
            return

        creator_ids = [f["followed_id"] for f in follows if f["type"] == "creator"]
        level_ids = [f["followed_id"] for f in follows if f["type"] == "level"]

        creators = db.get_creators(creator_ids)
        levels = db.get_info(level_ids)

        embed = discord.Embed(title="Your Follows", color=0x00ff00)

        if creator_ids:
            creator_list = "\n".join(f"• {creators[cid]['name']} ({cid})" for cid in creator_ids if cid in creators)
            embed.add_field(name="Creators", value=creator_list or "None", inline=False)

        if level_ids:
            level_list = "\n".join(f"• {levels[lid]['name']} ({lid})" for lid in level_ids if lid in levels)
            embed.add_field(name="Levels", value=level_list or "None", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unfollow", description="Unfollow a creator or level")
    async def unfollow(self, interaction: discord.Interaction, type: Literal["creator", "level"], id: int):
        db.remove_follow(interaction.user.id, type, id)
        await interaction.response.send_message(f"✅ Unfollowed {type} `{id}`", ephemeral=True)

async def notify_followers(level_info: dict, timestamp: datetime):
    level_followers = db.get_followers("level", level_info["_id"])
    creator_followers = db.get_followers("creator", level_info["creator"])

    followers = set(level_followers + creator_followers)

    embed = discord.Embed(
        title=f"{level_info['name']} was just sent!",
        description=f"By **{level_info['creator']}**\nTotal Sends: **{level_info['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_info['_id']})",
        color=0x00ff00
    )

    embed.set_author(name=level_info["creator"], url=f"https://gdbrowser.com/u/{level_info['creatorID']}", icon_url="https://gdbrowser.com/assets/cp.png")
    embed.timestamp = timestamp
    
    for follower_id in followers:
        try:
            user = await client.fetch_user(follower_id)
            await user.send(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            continue

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
        await interaction.response.send_message(f"{'⚠️ **WARNING**: This level was created before the bot started tracking levels. Any sends before the bot started operating have not been counted.\n\n' if level_id < OLDEST_LEVEL else ''}❌ Level `{level_id}` has no sends.", ephemeral=True)
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
        color=0x00ff00 if level_id >= OLDEST_LEVEL else 0xff0000
    )
    if creatorString:
        embed.set_author(name=levelData["creatorName"], url=f"https://gdbrowser.com/u/{levelData['accountID']}", icon_url="https://gdbrowser.com/assets/cp.png")

    await interaction.response.send_message(content='⚠️ **WARNING**: This level was created before the bot started tracking levels. The data may be inaccurate.' if level_id < OLDEST_LEVEL else '', embed=embed)

@client.tree.command(name="leaderboard", description="Show the send leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    view = LeaderboardView(db, interaction.user.id)
    await interaction.response.send_message(embed=await view.get_embed(), view=view)
    view.message = await interaction.original_response()

@client.tree.command(name="stats", description="Show the bot's stats.")
async def stats(interaction: discord.Interaction):
    total_sends = db.get_total_sends()
    total_creators = db.get_total_creators()
    total_levels = db.get_total_levels()
    oldest_level = db.get_oldest_level()
    oldest_creator = db.get_oldest_creator()
    latest_send = db.get_latest_send()

    embed = discord.Embed(
        title="Bot Stats",
        description=f"Total Sends: `{total_sends}`\nTotal Creators: `{total_creators}`\nTotal Levels: `{total_levels}`\nOldest Level: **{oldest_level["name"]}** ([GDBrowser](https://gdbrowser.com/{oldest_level["_id"]}))\nOldest Creator: **{oldest_creator["name"]}** ([GDBrowser](https://gdbrowser.com/u/{oldest_creator['accountID']}))\nLatest Send: <t:{int(latest_send['timestamp'].timestamp())}:F> (<t:{int(latest_send['timestamp'].timestamp())}:R>)",
        color=0x00ff00
    )

    await interaction.response.send_message(embed=embed)


client.run(environ.get("BOT_TOKEN"))
