import random
import discord, os, json, re, git, asyncio, logging
from dotenv import load_dotenv
from os import environ
from discord.ext import commands, tasks
from discord.ui import Button, View
from discord import app_commands
from datetime import datetime, UTC
from math import ceil
from enum import Enum
from typing import Literal

from db import SendDB
import utils

logging.basicConfig(
	filename='exceptions.log',  # File to save exceptions
	level=logging.ERROR,        # Log level
	format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv()

connection_string = environ.get('MONGO_CONNECTION_STRING')
if connection_string is None:
	raise EnvironmentError("MONGO_CONNECTION_STRING environment variable is not set.")

db = SendDB(connection_string)

OLDEST_LEVEL = int(environ.get("OLDEST_LEVEL"))
DIFFICULTIES = {
	1: "Auto 1⭐",
	2: "Easy 2⭐",
	3: "Normal 3⭐",
	4: "Hard 4⭐",
	5: "Hard 5⭐",
	6: "Harder 6⭐",
	7: "Harder 7⭐",
	8: "Insane 8⭐",
	9: "Insane 9⭐",
	10: "Demon 10⭐"
}
DEMON_DIFFICULTY_MAP = {
	0: "Easy ",
	1: "Medium ",
	2: "Hard ",
	3: "Insane ",
	4: "Extreme "
}
RATINGS = {
	1: "Rate",
	2: "Feature",
	3: "Epic",
	4: "Legendary",
	5: "Mythic"
}

# Custom check for moderator permissions
async def is_moderator(interaction: discord.Interaction) -> bool:
	"""Check if a user is a moderator"""
	# Check if the user is in the moderators collection
	if db.is_moderator(interaction.user.id):
		return True

	# If not a moderator, let them know
	await interaction.response.send_message("You don't have permission to use this command. Only moderators can use it.", ephemeral=True)
	return False

def get_git_info():
	repo = git.Repo(search_parent_directories=True)
	commit_hash = repo.head.commit.hexsha
	upstream_url = next(repo.remote('origin').urls)
	return commit_hash, upstream_url

commit_hash, upstream_url = get_git_info()
invite = environ.get("INVITE")

def load_previous_data():
	if os.path.exists("previous_data.json"):
		with open("previous_data.json", "r") as file:
			data = json.load(file)
			return data
	return {}

def save_previous_data(levels, rated_levels):
	data = {
		"previous_levels": levels,
		"previous_rated_levels": rated_levels,
		"trending_message": client.trendingMessageID
	}
	with open("previous_data.json", "w") as file:
		json.dump(data, file)

previous_data = load_previous_data()
previous_levels = previous_data.get("previous_levels", [])
previous_rated_levels = previous_data.get("previous_rated_levels", [])

def calculateNewSends(levels: list[int], rated_levels: list[int]) -> tuple[list[int], list[int]]:
	global previous_levels, previous_rated_levels

	filtered_levels = [level for level in levels if level not in rated_levels]

	if not previous_levels:
		previous_levels = levels.copy()
		previous_rated_levels = rated_levels.copy()
		return filtered_levels.copy(), rated_levels.copy()

	rates = [level for level in rated_levels if level not in previous_rated_levels]

	prev_levels_working = previous_levels.copy()
	ignore_count = 0

	for level in rates:
		if level in prev_levels_working:
			prev_levels_working.remove(level)
			ignore_count += 1

	check_limit = len(filtered_levels) - ignore_count

	max_bumps = 0

	for i in range(check_limit):
		level = filtered_levels[i]
		prev_index = prev_levels_working.index(level) if level in prev_levels_working else float('inf')

		if i < prev_index:
			bumps_after = i
			total_bumps_including_this = bumps_after + 1
			max_bumps = max(max_bumps, total_bumps_including_this)

	sends = filtered_levels[:max_bumps]
	sends.reverse()

	previous_levels = levels.copy()
	previous_rated_levels = rated_levels.copy()
	save_previous_data(previous_levels, previous_rated_levels)

	return sends, rates

async def onSendResults(levels: list[dict], creators: list[dict], rated_levels: list[dict], rated_creators: list[dict]):
	if not levels or not creators or not rated_levels or not rated_creators:
		print("No data received.")
		return

	timestamp = datetime.now(UTC)

	creatorMap = {creator["_id"]: creator["name"] for creator in creators + rated_creators}
	accountMap = {creator["_id"]: creator["accountID"] for creator in creators + rated_creators}
	level_ids = [level["_id"] for level in levels]
	rated_level_ids = [level["_id"] for level in rated_levels]

	new_send_ids, new_rate_ids = calculateNewSends(level_ids, rated_level_ids)

	sends = []
	rates = []
	unrates = []
	info = []
	sendMessageInfo = []
	rateMessageInfo = []

	for level_id in new_send_ids:
		level = next(l for l in levels if l["_id"] == level_id)
		creator_id = level["creatorID"]
		creator_name = creatorMap.get(creator_id, "Unknown")

		sends.append({"levelID": level_id, "timestamp": timestamp})
		info.append({"_id": level_id, "name": level["name"], "creator": creator_id, "length": level["length"], "platformer": level["platformer"]})
		sendMessageInfo.append({
			"_id": level_id,
			"name": level["name"],
			"creator": creator_name,
			"creatorID": accountMap.get(creator_id, 0),
			"playerID": creator_id,
			"sends": 1
		})

	for level_id in new_rate_ids:
		level = next(l for l in rated_levels if l["_id"] == level_id)
		creator_id = level["creatorID"]

		rates.append({
			"_id": level_id,
			"difficulty": level["difficulty"],
			"stars": level["stars"],
			"points": level["points"],
			"timestamp": timestamp
		})
		info.append({"_id": level_id, "name": level["name"], "creator": creator_id, "length": level["length"], "platformer": level["platformer"]})

		stars = DIFFICULTIES.get(level["stars"], "Unknown")
		if level["stars"] == 10:
			stars = DEMON_DIFFICULTY_MAP.get(level["difficulty"], "") + stars

		rateMessageInfo.append({
			"_id": level_id,
			"name": level["name"],
			"creator": creatorMap.get(creator_id, "Unknown"),
			"creatorID": accountMap.get(creator_id, 0),
			"playerID": creator_id,
			"stars": stars,
			"points": level["points"],
			"rating": RATINGS.get(level["points"], "Unknown"),
			"sends": 0
		})

	db.add_sends(sends)
	db.add_rates(rates)
	db.add_info(info)

	if sends or rates or unrates:
		await client.update_trending_message()

	if sendMessageInfo:
		print("New sent levels!")
		db.add_creators(creators)
		checkIds = [level["_id"] for level in sendMessageInfo]
		sendMap = db.get_sends(checkIds)
		for sendID, sendCount in sendMap.items():
			for level in sendMessageInfo:
				if level["_id"] == sendID:
					level["sends"] = sendCount["count"]
					asyncio.create_task(notify_followers_of_send(level, timestamp))

		asyncio.create_task(sendSendsMessage(sendMessageInfo, timestamp))

	if rateMessageInfo:
		print("New rated levels!")
		db.add_creators(rated_creators)
		checkIds = [level["_id"] for level in rateMessageInfo]
		sendMap = db.get_sends(checkIds)
		for rateID, sendCount in sendMap.items():
			for level in rateMessageInfo:
				if level["_id"] == rateID:
					level["sends"] = sendCount["count"]
					asyncio.create_task(notify_followers_of_rate(level, timestamp))

		asyncio.create_task(sendRatesMessage(rateMessageInfo, timestamp))

async def sendBanNotification():
	await client.sendChannel.send("❌ **Bot was IP Banned!**")

checker = utils.SentChecker(onSendResults, sendBanNotification, db)

class SendBot(commands.Bot):
	def __init__(self):
		super().__init__(command_prefix='=', intents=discord.Intents.none())
		self.sendChannel = None
		self.trendingChannel = None
		self.trendingMessageID = previous_data.get("trending_message", None)
		self.trendingMessage = None
		self.synced = False
		self.tips = []

	async def setup_hook(self):
		"""This method is called before on_ready to set up initial things"""
		# Set up error handler for app commands
		self.tree.on_error = self.on_app_command_error

	async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
		"""Event that triggers when a command is successfully executed"""
		# Increment the commands run counter in the database
		db.increase_stat("commands")

		# Optional: You can add more detailed stats if needed
		command_name = command.qualified_name
		db.increase_stat(f"command_{command_name}")

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

		self.trendingChannel = (self.get_channel(int(environ.get('TRENDING_CHANNEL_ID'))) or await self.fetch_channel(int(environ.get('TRENDING_CHANNEL_ID'))))
		if self.trendingChannel:
			self.update_trending_message.start()

		checker.start(asyncio.get_running_loop())
		print(f"We have logged in as {self.user}.")

		client.tips = [
			f"🌟 **Checking levels often**? Get DM notifications for new sends by following creators with {await self.get_full_command_embed('follow creator')}!",
			f"📈 **Want to see the most popular levels?** Check out the trending levels with {await self.get_full_command_embed('trending')}!",
			f"🔍 **Looking for a creator's stats?** Use {await self.get_full_command_embed('check-creator')} to see their sends and levels!",
			f"👀 **Want to see a level's stats?** Use {await self.get_full_command_embed('check-level')} to view its sends!",
			f"📊 **Want to see the leaderboard?** Use {await self.get_full_command_embed('leaderboard')} to view the top creators and levels!",
			f"💬 **Need help or have suggestions?** Join our support server [here](https://discord.gg/{invite})!",
		]

	async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
		"""Event that triggers when a command encounters an error"""
		# Log the error to the file
		logging.error(f"Command error occurred for user {interaction.user.id}", exc_info=error)

		# Notify the user if they haven't already been responded to
		if not interaction.response.is_done():
			await interaction.response.send_message(
				"There was an error executing this command. Please try again later.",
				ephemeral=True
		)

	async def close(self):
		checker.stop()
		await super().close()

	async def get_command_id(self, command_name: str):
		bot_commands = await self.tree.fetch_commands()
		for command in bot_commands:
			if command.name == command_name:
				return command.id
		return None

	@tasks.loop(minutes=1)
	async def update_trending_message(self):
		try:
			trending_levels, _ = db.get_trending_levels()
			embed = discord.Embed(
				title="🔥 Trending Levels",
				description="Most popular levels in the last 30 days",
				color=0xff6600
			)

			for idx, level in enumerate(trending_levels, 1):
				medal = ""

				if idx == 1:
					medal = "🥇"
				elif idx == 2:
					medal = "🥈"
				elif idx == 3:
					medal = "🥉"

				embed.add_field(
					name=f"{medal}#{idx}. {level['name']} ({level['levelID']})",
					value=f"By **{level['creator']}** ({level['creatorID']})\n"
						  f"Recent Sends: **{level['recent_sends']}**\n"
						  f"Last Send: <t:{int(level['latest_send'].timestamp())}:R>\n"
						  f"Score: `{int(level['score'])}`",
					inline=False
				)

			embed.timestamp = datetime.now(UTC)
			command_id = await self.get_command_id("trending")
			content = f"View the full leaderboard with </trending:{command_id}>" if command_id else ""

			if self.trendingMessage:
				await self.trendingMessage.edit(embed=embed, content=content)
			if self.trendingMessageID:
				self.trendingMessage = await self.trendingChannel.fetch_message(self.trendingMessageID)
				await self.trendingMessage.edit(embed=embed, content=content)
			else:
				self.trendingMessage = await self.trendingChannel.send(embed=embed, content=content)
				self.trendingMessageID = self.trendingMessage.id
				save_previous_data(previous_levels, previous_rated_levels)

		except Exception as e:
			print(f"Error updating trending message: {e}")

	async def get_full_command_embed(self, command_name: str) -> str:
		return f"</{command_name}:{await self.get_command_id(command_name)}>" if await self.get_command_id(command_name) else f"`/{command_name}`"

client = SendBot()

async def sendRandomTip(interaction: discord.Interaction, exclude: list[int] = None) -> None:
	if random.randint(1, 10) >= 3: return

	if exclude is None:
		exclude = []
	if not client.tips:
		return
	tips = [client.tips[i] for i in range(len(client.tips)) if i not in exclude]

	await interaction.followup.send(
		random.choice(tips) + "\n-# This is a tip. Feel free to ignore it if you don't need it.",
		ephemeral=True,
	)

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
		self._view = parent_view
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

class FilterSelect(discord.ui.Select):
	def __init__(self, parent_view: "LeaderboardView"):
		self._view = parent_view
		options = [
			discord.SelectOption(label="Rated", value="RATED", description="Show rated levels"),
			discord.SelectOption(label="Unrated", value="UNRATED", description="Show unrated levels"),
			discord.SelectOption(label="Classic", value="CLASSIC", description="Show classic levels"),
			discord.SelectOption(label="Platformer", value="PLATFORMER", description="Show platformer levels"),
		]
		super().__init__(placeholder="Select search filters...", options=options)

	async def callback(self, interaction: discord.Interaction):
		self._view.filters = self.values
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
			label=f"{'User/Player' if search_type == LeaderboardType.CREATORS else 'Level'} ID{' (NOT Account ID)' if search_type == LeaderboardType.CREATORS else ''}",
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
		self.filters = []
		self.searched_id = None

		# Add type selector
		self.type_select = TypeSelect(self)
		self.add_item(self.type_select)
		self.filter_select = FilterSelect(self)
		self.add_item(self.filter_select)

	def get_pipeline_for_type(self, skip: int, limit: int) -> list[dict]:
		base_pipeline = [
			{"$group": {
				"_id": "$levelID",
				"send_count": {"$sum": 1}
			}},
			{"$lookup": {
				"from": "info",
				"localField": "_id",
				"foreignField": "_id",
				"as": "level_info"
			}},
			{"$unwind": "$level_info"}
		]

		if self.filters:
			match_conditions = {}
			if self.filters.__contains__("RATED") != self.filters.__contains__("UNRATED"):
				base_pipeline.append({
					"$lookup": {
						"from": "rates",
						"localField": "_id",
						"foreignField": "_id",
						"as": "rate_info"
					}
				})
				if "RATED" in self.filters:
					match_conditions["rate_info"] = {"$ne": []}
				elif "UNRATED" in self.filters:
					match_conditions["rate_info"] = {"$eq": []}
			if "PLATFORMER" in self.filters:
				match_conditions["level_info.platformer"] = True
			if "CLASSIC" in self.filters:
				match_conditions["level_info.platformer"] = False

			if match_conditions:
				base_pipeline.append({"$match": match_conditions})

		if self.type == LeaderboardType.CREATORS:
			base_pipeline.extend([
				{"$group": {
					"_id": "$level_info.creator",
					"sends": {"$sum": "$send_count"},
					"level_count": {"$sum": 1}
				}},
				{"$lookup": {
					"from": "creators",
					"localField": "_id",
					"foreignField": "_id",
					"as": "creator_info"
				}},
				{"$unwind": "$creator_info"},
				{"$project": {
					"name": "$creator_info.name",
					"accountID": "$creator_info.accountID",
					"sends": 1,
					"level_count": 1
				}}
			])
		else:
			base_pipeline.extend([
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
					"creatorID": "$creator_info.accountID",
					"levelID": "$_id",
					"sends": "$send_count"
				}}
			])

		base_pipeline.append({
			"$facet": {
				"total": [{"$count": "count"}],
				"data": [
					{"$sort": {"sends": -1, "_id": 1}},
					{"$skip": skip},
					{"$limit": limit}
				]
			}
		})

		return base_pipeline


	async def get_page_data(self) -> tuple[list[dict], int]:
		skip = self.current_page * self.page_size
		pipeline = self.get_pipeline_for_type(skip, self.page_size)

		result = self.db.raw_pipeline("sends", pipeline)
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

		result = self.db.raw_pipeline("sends", pipeline)
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

class TrendingView(View):
	def __init__(self, db: SendDB, owner_id: int, page_size: int = 10):
		super().__init__(timeout=180)
		self.db = db
		self.owner_id = owner_id
		self.page_size = page_size
		self.current_page = 0
		self.total_count = 0
		self.max_pages = 1

	async def get_page_data(self) -> tuple[list[dict], int]:
		skip = self.current_page * self.page_size
		return self.db.get_trending_levels(skip, self.page_size, True)

	def update_buttons(self):
		self.prev_button.disabled = self.current_page == 0
		self.next_button.disabled = self.current_page >= self.max_pages - 1

	async def get_embed(self) -> discord.Embed:
		page_data, self.total_count = await self.get_page_data()
		self.max_pages = ceil(self.total_count / self.page_size)

		embed = discord.Embed(
			title="🔥 Trending Levels",
			description="Most popular levels in the last 30 days",
			color=0xff6600
		)

		start_idx = self.current_page * self.page_size

		for idx, level in enumerate(page_data, start=start_idx + 1):
			medal = ""

			if idx == 1:
				medal = "🥇"
			elif idx == 2:
				medal = "🥈"
			elif idx == 3:
				medal = "🥉"

			embed.add_field(
				name=f"{medal}#{idx}. {level['name']} ({level['levelID']})",
				value=f"By **{level['creator']}** ({level['creatorID']})\n"
					  f"Recent Sends: **{level['recent_sends']}**\n"
					  f"Last Send: <t:{int(level['latest_send'].timestamp())}:R>\n"
					  f"Score: `{int(level['score'])}`",
				inline=False
			)

		embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages} • Total Levels: {self.total_count}")
		embed.timestamp = datetime.now(UTC)
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

# Helper function to extract ID from a string (used for autocompletes)
def extract_id(input_string):
	"""Extract a numeric ID from a string that might contain a name and ID"""
	if match := re.search(r'\((\d+)\)', input_string):
		return int(match.group(1))

	if match := re.search(r'(\d+)$', input_string):
		return int(match.group(1))

	if input_string.isdigit():
		return int(input_string)

	return None

async def level_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
	if not current or '(' in current or len(current) > 20:
		return []
	levels = db.search_levels(current)
	return [
		app_commands.Choice(name=f"{level['name']} ({level['_id']})", value=str(level['_id']))
		for level in levels
	][:25]

async def creator_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
	if not current or '(' in current or len(current) > 16:
		return []
	creators = db.search_creators(current)
	return [
		app_commands.Choice(name=f"{creator['name']} ({creator['_id']})", value=str(creator['_id']))
		for creator in creators
	][:25]

class FollowCommands(commands.GroupCog, name="follow"):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		super().__init__()

	@app_commands.command(name="creator", description="Follow a creator to get DM notifications when their levels are sent")
	@app_commands.autocomplete(creator=creator_autocomplete)
	async def follow_creator(self, interaction: discord.Interaction, creator: str):
		await interaction.response.defer(ephemeral=True)
		creator_id = extract_id(creator)

		# Verify creator exists
		creators = db.get_creators([creator_id])
		if creator_id not in creators:
			if not creator.isdigit():
				if checker.is_user_pending(interaction.user.id):
					await interaction.followup.send("You already have a pending creator check. Please wait for it to complete.", ephemeral=True)
					return

				# Try to find creator by name
				async def callback(username: str, player_id: int, account_id: int):
					if username == "" and player_id == 0 and account_id == 0:
						await interaction.followup.send("❌ Creator not found", ephemeral=True)
						return
					db.add_creators([{"_id": player_id, "name": username, "accountID": account_id}])
					db.add_follow(interaction.user.id, "creator", player_id)
					await interaction.followup.send(f"✅ Now following **{username}** with ID: `{player_id}`", ephemeral=True)

					await sendRandomTip(interaction, exclude=[0])

				timeNow = int(datetime.now(UTC).timestamp())
				checker.queue_check(creator, callback, interaction.user.id)
				await interaction.followup.send(f"🔍 Checking creator `{creator}` (Ready <t:{timeNow+checker.approximate_wait_time(interaction.user.id)}:R>)...", ephemeral=True)
				return

			await interaction.followup.send(f"❌ Creator `{creator}` not found", ephemeral=True)
			return

		db.add_follow(interaction.user.id, "creator", creator_id)
		await interaction.followup.send(f"✅ Now following **{creators[creator_id]['name']}**", ephemeral=True)

		await sendRandomTip(interaction, exclude=[0])

	@app_commands.command(name="level", description="Follow a level to get DM notifications when it is sent")
	async def follow_level(self, interaction: discord.Interaction, level_id: int):
		level_info = db.get_info([level_id])
		if level_id in level_info:
			level_name = f"**{level_info[level_id]['name']}**"
		else:
			level_name = f"`{level_id}`"

		db.add_follow(interaction.user.id, "level", level_id)
		await interaction.response.send_message(f"✅ Now following level {level_name}", ephemeral=True)

		await sendRandomTip(interaction, exclude=[0])

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

		await sendRandomTip(interaction, exclude=[0])

	@app_commands.command(name="unfollow", description="Unfollow a creator or level")
	async def unfollow(self, interaction: discord.Interaction, type: Literal["creator", "level"], id: int):
		db.remove_follow(interaction.user.id, type, id)
		await interaction.response.send_message(f"✅ Unfollowed {type} `{id}`", ephemeral=True)

		await sendRandomTip(interaction, exclude=[0])

async def notify_followers_of_send(level_info: dict, timestamp: datetime):
	level_followers = db.get_followers("level", level_info["_id"])
	creator_followers = db.get_followers("creator", level_info["playerID"])

	followers = set(level_followers + creator_followers)

	embed = discord.Embed(
		title=f"{level_info['name']} was just sent!",
		description=f"By **{level_info['creator']}**\nTotal Sends: **{level_info['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_info['_id']})",
		color=0x00ff00
	)

	if level_info["creatorID"] != 0: url = f"u/{level_info['creatorID']}"
	else: url = f"search/{level_info['playerID']}?user"

	embed.set_author(name=level_info["creator"], url=f"https://gdbrowser.com/{url}", icon_url="https://gdbrowser.com/assets/cp.png")
	embed.timestamp = timestamp

	for follower_id in followers:
		try:
			user = await client.fetch_user(follower_id)
			await user.send(embed=embed)
		except (discord.NotFound, discord.Forbidden):
			continue

async def notify_followers_of_rate(level_info: dict, timestamp: datetime):
	level_followers = db.get_followers("level", level_info["_id"])
	creator_followers = db.get_followers("creator", level_info["creator"])

	followers = set(level_followers + creator_followers)

	embed = discord.Embed(
		title=f"{level_info['name']} was just rated!",
		description=f"Difficulty: **{level_info['stars']}**\nRating: **{level_info['rating']}** (+**{level_info['points']}**)\nBy **{level_info['creator']}**\nTotal Sends: **{level_info['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_info['_id']})",
		color=0xd4af37
	)

	if level_info["creatorID"] != 0: url = f"u/{level_info['creatorID']}"
	else: url = f"search/{level_info['playerID']}?user"

	embed.set_author(name=level_info["creator"], url=f"https://gdbrowser.com/{url}", icon_url="https://gdbrowser.com/assets/cp.png")
	embed.timestamp = timestamp

	for follower_id in followers:
		try:
			user = await client.fetch_user(follower_id)
			await user.send(embed=embed)
		except (discord.NotFound, discord.Forbidden):
			continue

async def sendSendsMessage(info: list[dict], timestamp: datetime):
	embeds = []
	for level in info:
		embed = discord.Embed(
			title=level["name"],
			description=f"By **{level['creator']}** ({level['playerID']})\nTotal Sends: **{level['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level['_id']}) (`{level['_id']}`)",
			color=0x00ff00
		)

		if level["creatorID"] != 0: url = f"u/{level['creatorID']}"
		else: url = f"search/{level['playerID']}?user"

		embed.set_author(name=level["creator"], url=f"https://gdbrowser.com/{url}", icon_url="https://gdbrowser.com/assets/cp.png")
		embed.timestamp = timestamp

		embeds.append(embed)

	num = len(info)
	s = "s" if num != 1 else ""
	message = await client.sendChannel.send(content=f"**{num}** level{s} sent.\nCheck time: <t:{int(timestamp.timestamp())}:F> (<t:{int(timestamp.timestamp())}:R>)", embeds=embeds)
	try:
		await message.publish()
	except discord.Forbidden:
		pass

async def sendRatesMessage(info: list[dict], timestamp: datetime):
	embeds = []
	for level in info:
		embed = discord.Embed(
			title=level["name"],
			description=f"Difficulty: **{level['stars']}**\nRating: **{level['rating']}** (+**{level['points']}**)\nBy **{level['creator']}** ({level['playerID']})\nTotal Sends: **{level['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level['_id']}) (`{level['_id']}`)",
			color=0xd4af37
		)

		if level["creatorID"] != 0: url = f"u/{level['creatorID']}"
		else: url = f"search/{level['playerID']}?user"

		embed.set_author(name=level["creator"], url=f"https://gdbrowser.com/{url}", icon_url="https://gdbrowser.com/assets/cp.png")
		embed.timestamp = timestamp

		embeds.append(embed)

	num = len(info)
	s = "s" if num != 1 else ""
	message = await client.sendChannel.send(content=f"**{num}** level{s} rated.\nCheck time: <t:{int(timestamp.timestamp())}:F> (<t:{int(timestamp.timestamp())}:R>)", embeds=embeds)
	try:
		await message.publish()
	except discord.Forbidden:
		pass

@client.tree.command(name="subscribe", description="Subscribe this channel to level send notifications.")
@app_commands.describe()
@app_commands.default_permissions(manage_channels=True)
async def subscribe(interaction: discord.Interaction):
	if not isinstance(interaction.channel, discord.TextChannel):
		await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
		return

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
@app_commands.describe(level="Enter a level name or ID to check its sends")
@app_commands.autocomplete(level=level_autocomplete)
async def check_level(interaction: discord.Interaction, level: str):
	# Extract the numeric ID from the input
	level_numeric_id = extract_id(level)

	if level_numeric_id is None:
		await interaction.response.send_message(f"❌ Invalid level ID: `{level}`. Please provide a valid level ID or select from the autocomplete list.", ephemeral=True)
		return

	sendData = db.get_sends([level_numeric_id])
	if level_numeric_id not in sendData:
		await interaction.response.send_message(f"{'⚠️ **WARNING**: This level was created before the bot started tracking levels. Any sends before the bot started operating have not been counted.\n\n' if level_numeric_id < OLDEST_LEVEL else ''}❌ Level `{level_numeric_id}` has no sends.", ephemeral=True)
		return
	sendCount = sendData[level_numeric_id]["count"]
	lastSend: datetime = sendData[level_numeric_id]["latest_timestamp"]

	infoData = db.get_info([level_numeric_id])
	if level_numeric_id not in infoData:
		levelData = {
			"id": level_numeric_id,
			"name": str(level_numeric_id)
		}
		creatorString = ""

	else:
		levelData = infoData[level_numeric_id]
		levelData["id"] = level_numeric_id  # Ensure the ID is included for the view
		creatorData = db.get_creators([levelData["creator"]])

		if levelData["creator"] not in creatorData:
			levelData["creatorName"] = "Unknown"
			creatorString = ""

		else:
			levelData["creatorName"] = creatorData[levelData["creator"]]["name"]
			levelData["accountID"] = creatorData[levelData["creator"]]["accountID"]
			creatorString = f"By **{levelData['creatorName']}** ({levelData['creator']})\n"

	# Create the basic embed
	embed = discord.Embed(
		title=f"{levelData['name']}",
		description=f"{creatorString}Total Sends: **{sendCount}**\nLast Sent: <t:{int(lastSend.timestamp())}:F> (<t:{int(lastSend.timestamp())}:R>)\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_numeric_id}) (`{level_numeric_id}`)",
		color=0x00ff00 if level_numeric_id >= OLDEST_LEVEL else 0xff0000
	)

	if creatorString:
		embed.set_author(name=levelData["creatorName"], url=f"https://gdbrowser.com/u/{levelData['accountID']}", icon_url="https://gdbrowser.com/assets/cp.png")

	levelData["id"] = level_numeric_id

	await interaction.response.send_message(
		content='⚠️ **WARNING**: This level was created before the bot started tracking levels. The data may be inaccurate.' if level_numeric_id < OLDEST_LEVEL else '',
		embed=embed
	)

	await sendRandomTip(interaction, exclude=[3])

@client.tree.command(name="check-creator", description="Check a creator's info.")
@app_commands.describe(creator="Enter a creator name or ID to check their info")
@app_commands.autocomplete(creator=creator_autocomplete)
async def check_creator(interaction: discord.Interaction, creator: str):
	# Extract the numeric ID from the input
	creator_numeric_id = extract_id(creator)

	if creator_numeric_id is None:
		await interaction.response.send_message(
			f"❌ Invalid creator ID: `{creator}`. Please provide a valid creator ID or select from the autocomplete list.",
			ephemeral=True
		)
		return

	creatorData = db.get_creator_info(creator_numeric_id)
	if not creatorData:
		await interaction.response.send_message(
			f"❌ Creator `{creator_numeric_id}` has no sends.",
			ephemeral=True
		)
		return

	embed = discord.Embed(
		title=f"{creatorData['name']}'s Stats",
		description=f"Account ID: `{creatorData['accountID']}`\nCreator Info: [GDBrowser](https://gdbrowser.com/u/{creatorData['accountID']})\n",
		color=0x00ff00
	)

	if creatorData['level_count'] == 0:
		average_sends = 0
	else:
		average_sends = creatorData['sends_count'] / creatorData['level_count']

	embed.add_field(name="Total Sends", value=f"**{creatorData['sends_count']}**", inline=True)
	embed.add_field(name="Level Count", value=f"**{creatorData['level_count']}**", inline=True)
	embed.add_field(name="Average Sends per Level", value=f"**{average_sends:.2f}**", inline=True)
	embed.add_field(name="Followers", value=f"**{creatorData['followers_count']}**", inline=True)
	embed.add_field(
		name="Latest Send",
		value=f"<t:{int(creatorData['latest_send'].timestamp())}:F> (<t:{int(creatorData['latest_send'].timestamp())}:R>)"
		if creatorData['latest_send'] else "None",
		inline=False
	)
	embed.set_footer(text=f"User ID: {creator_numeric_id}")

	await interaction.response.send_message(embed=embed)

	await sendRandomTip(interaction, exclude=[2])

@client.tree.command(name="leaderboard", description="Show the send leaderboard.")
async def leaderboard(interaction: discord.Interaction):
	view = LeaderboardView(db, interaction.user.id)
	await interaction.response.send_message(embed=await view.get_embed(), view=view)
	view.message = await interaction.original_response()

	await sendRandomTip(interaction, exclude=[4])

@client.tree.command(name="trending", description="Show currently trending levels")
async def trending(interaction: discord.Interaction):
	view = TrendingView(db, interaction.user.id)
	await interaction.response.send_message(embed=await view.get_embed(), view=view)
	view.message = await interaction.original_response()

	await sendRandomTip(interaction, exclude=[1])

@client.tree.command(name="info", description="Show the bot's info and stats.")
async def info(interaction: discord.Interaction):
	commands = db.get_stat("commands")
	requests = db.get_stat("requests")
	total_sends = db.get_total_sends()
	total_creators = db.get_total_creators()
	total_levels = db.get_total_levels()
	oldest_level = db.get_oldest_level()
	oldest_creator = db.get_oldest_creator()
	latest_send = db.get_latest_send()

	embed = discord.Embed(
		title="Bot Stats",
		description=f"""
Total Servers: `{len(client.guilds)}`
Total Commands Run: `{commands}`
Total Requests: `{requests}`

Total Sends: `{total_sends}`
Total Creators: `{total_creators}`
Total Levels: `{total_levels}`
Oldest Level: **{oldest_level["name"]}** ([GDBrowser](https://gdbrowser.com/{oldest_level["_id"]}))
Oldest Creator: **{oldest_creator["name"]}** ([GDBrowser](https://gdbrowser.com/u/{oldest_creator['accountID']}))
Latest Send: <t:{int(latest_send['timestamp'].timestamp())}:F> (<t:{int(latest_send['timestamp'].timestamp())}:R>)

Version: `{commit_hash[:7]}` ([View on GitHub]({upstream_url}/tree/{commit_hash}))
Support Server: [SendDB](https://discord.gg/{invite})
		""",
		color=0x00ff00
	)

	await interaction.response.send_message(embed=embed)

	await sendRandomTip(interaction, exclude=[5])

async def send_tips(interaction: discord.Interaction):
	interaction.followup.send()

client.run(environ.get("BOT_TOKEN"))