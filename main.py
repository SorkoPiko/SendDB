import asyncio, math
import logging
import sys
from dotenv import load_dotenv
from os import environ
import discord, os, json, re, git
from discord.ext import commands, tasks
from discord.ui import Button, View
from discord import app_commands
from datetime import datetime, timezone, timedelta, UTC
from math import ceil
from enum import Enum
from typing import Literal, Optional, Union, Dict, List, Tuple
from mongoengine.errors import ValidationError

from db import SendDB
from utils import SentChecker

load_dotenv()
db = SendDB(f"mongodb+srv://{environ.get('MONGO_USERNAME')}:{environ.get('MONGO_PASSWORD')}@{environ.get('MONGO_ENDPOINT')}")

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
	return []

def save_previous_data(levels):
	data = {
		"previous_levels": levels,
		"trending_message": client.trendingMessageID
	}
	with open("previous_data.json", "w") as file:
		json.dump(data, file)

previous_data = load_previous_data()
previous_levels = previous_data.get("previous_levels", [])

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
				"playerID": levels[i]["creatorID"],
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
					await client.update_trending_message()

		await sendMessage(webhookInfo, timestamp)

async def sendBanNotification():
	await client.sendChannel.send("❌ **Bot was IP Banned!**")

checker = SentChecker(onSendResults, sendBanNotification)

class SendBot(commands.Bot):
	def __init__(self):
		super().__init__(command_prefix='=', intents=discord.Intents.none())
		self.sendChannel = None
		self.trendingChannel = None
		self.trendingMessageID = previous_data.get("trending_message", None)
		self.trendingMessage = None
		self.update_trending_message.start()
		self.weekly_mod_reminder.start()  # Start the weekly moderator reminder task

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
				save_previous_data(previous_levels)

		except Exception as e:
			print(f"Error updating trending message: {e}")

	@tasks.loop(hours=24)
	async def weekly_mod_reminder(self):
		"""Send weekly reminders to moderators about pending level suggestions."""
		# Only send reminder on Sundays
		if datetime.utcnow().weekday() != 6:  # 6 is Sunday (0 is Monday)
			return
            
		# Get all moderators
		moderators = db.get_all_moderators()
		if not moderators:
			return
            
		# Send a personalized message to each moderator
		for mod in moderators:
			try:
				# Get count specific to this moderator
				mod_id = mod["discord_id"]
				mod_pending_count = db.get_pending_suggestion_count(mod_id)
                
				if mod_pending_count <= 0:
					continue  # Skip if this moderator has already reviewed all levels
                    
				command_id = await self.get_command_id("pending-suggestions")
				content = f"</pending-suggestions:{command_id}>" if command_id else "/pending-suggestions"

				# Create a personalized reminder embed
				embed = discord.Embed(
					title="🔔 Weekly Moderator Reminder",
					description=f"There are **{mod_pending_count}** level suggestions waiting for your review. Use {content} to start reviewing.",
					color=0x00aaff
				)
                
				mod_position = db.get_moderator_position(mod_id)

				if mod_position > 0:
					embed.add_field(
						name="Your Progress",
						value=f"You're in position **#{mod_position}** out of `{len(moderators)}` moderators. Keep up the good work!",
						inline=False
					)

				embed.set_footer(text="This is a weekly reminder for moderators to review pending suggestions.")
                
				# Send DM to the moderator
				user = await self.fetch_user(mod_id)
				await user.send(embed=embed)
			except (discord.NotFound, discord.Forbidden):
				continue
			except Exception as e:
				print(f"Error sending reminder to moderator {mod.get('discord_id')}: {e}")
                
		print(f"Sent weekly reminders to moderators about pending suggestions")

	@weekly_mod_reminder.before_loop
	async def before_reminder(self):
		"""Wait until the bot is ready before starting the reminder loop."""
		await self.wait_until_ready()
            
		# If we're not on Sunday, wait until next Sunday
		now = datetime.now(UTC)
		days_until_sunday = (6 - now.weekday()) % 7  # Days until next Sunday
		if days_until_sunday > 0:
			# Wait until next Sunday at 15:00 UTC (a good time for most regions)
			next_sunday = now + timedelta(days=days_until_sunday)
			target_time = datetime(
				next_sunday.year, next_sunday.month, next_sunday.day, 
				15, 0, 0  # 15:00 UTC
			)
			await asyncio.sleep((target_time - now).total_seconds())

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

class ModReviewView(View):
	def __init__(self, db: SendDB, owner_id: int):
		super().__init__(timeout=1800)  # 30 minute timeout
		self.db = db
		self.owner_id = owner_id
		self.page = 0
		self.page_size = 1  # Show one level at a time
		self.message = None
		self.current_level_id = None
		
	async def get_page_data(self) -> tuple[list[dict], int]:
		# Only filter out levels this specific moderator has rated
		return self.db.get_pending_suggestions(self.page, self.page_size, self.owner_id)
		
	def update_buttons(self, current_data):
		self.prev_button.disabled = self.page == 0
		self.next_button.disabled = not current_data  # Disable if no data
		
		# Only enable rating buttons if we have data
		if current_data:
			self.rate_button.disabled = False
			self.reject_button.disabled = False
		else:
			self.rate_button.disabled = True
			self.reject_button.disabled = True
		
	async def get_embed(self) -> discord.Embed:
		levels, total_count = await self.get_page_data()
		
		if not levels:
			embed = discord.Embed(
				title="No More Levels To Review",
				description="There are no more levels for you to review. Either all levels have been processed or you've already reviewed all available levels.",
				color=0x00aaff
			)
			return embed
		
		level = levels[0]  # Get the first (and only) level
		self.current_level_id = level["level_id"]
		
		# Get suggestion data
		#suggestions = self.db.get_user_suggestions(level["level_id"])
		weighted_avg = self.db.get_weighted_suggestion_average(level["level_id"])
		suggestion_score = self.db.get_suggestion_score(level["level_id"])
		
		# Create the embed
		embed = discord.Embed(
			title=f"Level Review: {level['level_name']}",
			description=f"ID: {level['level_id']}\nCreator: **{level['creator_name']}**\n"
						f"Reviewing level {self.page + 1} of {total_count}\n"
						f"[View on GDBrowser](https://gdbrowser.com/{level['level_id']})",
			color=0x00aaff
		)
		
		# Add suggestion summary information
		embed.add_field(
			name="Suggestion Summary",
			value=f"**Total Suggestions:** {level['suggestion_count']}\n"
					f"**Suggestion Score:** {suggestion_score}\n"
					f"**Latest Suggestion:** <t:{int(level['latest_suggestion'].timestamp())}:R>",
			inline=False
		)
		
		# Add weighted average information
		embed.add_field(
			name="Weighted Suggestion Average",
			value=f"**Difficulty:** {weighted_avg['difficulty']}/10 ({DIFFICULTIES[weighted_avg['difficulty']]})\n"
					f"**Rating:** {weighted_avg['rating']}/5 ({RATINGS[weighted_avg['rating']]})",
			inline=False
		)
		
		embed.set_footer(text="Choose to rate or reject the level")
		return embed
	
	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		return interaction.user.id == self.owner_id
	
	@discord.ui.button(label="Previous", style=discord.ButtonStyle.primary, emoji="⬅️")
	async def prev_button(self, interaction: discord.Interaction, button: Button):
		self.page = max(0, self.page - 1)
		levels, _ = await self.get_page_data()
		self.update_buttons(levels)
		await interaction.response.edit_message(embed=await self.get_embed(), view=self)
	
	@discord.ui.button(label="Next", style=discord.ButtonStyle.primary, emoji="➡️")
	async def next_button(self, interaction: discord.Interaction, button: Button):
		self.page += 1
		levels, _ = await self.get_page_data()
		self.update_buttons(levels)
		await interaction.response.edit_message(embed=await self.get_embed(), view=self)
	
	@discord.ui.button(label="Rate Level", style=discord.ButtonStyle.success, row=1)
	async def rate_button(self, interaction: discord.Interaction, button: Button):
		if not self.current_level_id:
			await interaction.response.send_message("No level is currently selected.", ephemeral=True)
			return
		
		# Get suggested values from weighted average
		weighted_avg = self.db.get_weighted_suggestion_average(self.current_level_id)
		suggested_difficulty = round(weighted_avg["difficulty"]) if weighted_avg["difficulty"] > 0 else 5
		suggested_rating = round(weighted_avg["rating"]) if weighted_avg["rating"] > 0 else 3
		
		# Create modal for both difficulty and rating inputs
		class RatingModal(discord.ui.Modal, title=f"Rate Level"):
			def __init__(self, outer_view):
				super().__init__()
				self.outer_view = outer_view
			
			difficulty = discord.ui.TextInput(
				label="Difficulty from 1 (Auto) to 10 (Demon)",
				placeholder="Enter a star value from 1 (Auto) to 10 (Demon)",
				default=str(suggested_difficulty),
				required=True,
				min_length=1,
				max_length=2
			)
			
			rating = discord.ui.TextInput(
				label="Rating from 1 (Rate) to 5 (Mythic)",
				placeholder="Enter a number from 1 (Rate) to 5 (Mythic)",
				default=str(suggested_rating),
				required=True,
				min_length=1,
				max_length=1
			)
			
			async def on_submit(self, interaction: discord.Interaction):
				try:
					difficulty_val = int(self.difficulty.value)
					rating_val = int(self.rating.value)
					
					if not (1 <= difficulty_val <= 10):
						await interaction.response.send_message("Invalid value. Difficulty must be 1-10.", ephemeral=True)
						return
						
					if not (1 <= rating_val <= 5):
						await interaction.response.send_message("Invalid value. Rating must be 1-5.", ephemeral=True)
						return
					
					# Store the rated level ID from the outer view
					rated_level_id = self.outer_view.current_level_id
					
					# Store the moderator's rating with both difficulty and quality rating
					self.outer_view.db.add_mod_rating(interaction.user.id, rated_level_id, difficulty_val, rating_val)
					
					await interaction.response.send_message(
						f"Your rating has been recorded:\n**Difficulty:** {DIFFICULTIES[difficulty_val]}\n**Rating:** {RATINGS[rating_val]}",
						ephemeral=True
					)
					
					# Keep track of the current page before refreshing
					current_page = self.outer_view.page
					
					# Refresh the current page to see updated data
					levels, _ = await self.outer_view.get_page_data()
					
					# If no more levels on current page, try to get data from other pages
					if not levels:
						# Try next page first (if we're not on page 0)
						if current_page > 0:
							# Try previous page
							self.outer_view.page = current_page - 1
							levels, _ = await self.outer_view.get_page_data()
							
							# If still no levels, try earlier pages
							page_to_try = current_page - 2
							while not levels and page_to_try >= 0:
								self.outer_view.page = page_to_try
								levels, _ = await self.outer_view.get_page_data()
								page_to_try -= 1
						
						# If still no levels, try next pages
						if not levels:
							self.outer_view.page = current_page + 1
							levels, _ = await self.outer_view.get_page_data()
							
							# If still no levels, keep trying next pages up to 5 more
							page_to_try = current_page + 2
							max_pages_to_try = current_page + 5
							while not levels and page_to_try <= max_pages_to_try:
								self.outer_view.page = page_to_try
								levels, _ = await self.outer_view.get_page_data()
								page_to_try += 1
					
					self.outer_view.update_buttons(levels)
					await self.outer_view.message.edit(embed=await self.outer_view.get_embed(), view=self.outer_view)
					
				except ValueError:
					await interaction.response.send_message("Please enter valid numbers for difficulty and rating.", ephemeral=True)
				except discord.errors.HTTPException as e:
					# If the message edit fails due to expired token, send a new message
					if e.code == 50027:  # Invalid Webhook Token
						await interaction.followup.send(
							"The review session has been updated. Please use the buttons on this message.",
							view=self.outer_view,
							embed=await self.outer_view.get_embed()
						)
						# Update message reference
						self.outer_view.message = await interaction.followup.fetch_message()
		
		# Create modal and set reference to the parent view
		modal = RatingModal(self)
		await interaction.response.send_modal(modal)
	
	@discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, row=1)
	async def reject_button(self, interaction: discord.Interaction, button: Button):
		if not self.current_level_id:
			await interaction.response.send_message("No level is currently selected.", ephemeral=True)
			return
			
		# Store the level ID before updating
		rejected_level_id = self.current_level_id
			
		# Store the moderator's rejection
		self.db.add_mod_rating(interaction.user.id, rejected_level_id, rejected=True)
		
		await interaction.response.send_message(
			f"You've rejected level {rejected_level_id}. Users who suggested it will have their accuracy negatively impacted.",
			ephemeral=True
		)
		
		# Keep track of the current page before refreshing
		current_page = self.page
		
		# Get current page data to check if there are still levels to review
		levels, _ = await self.get_page_data()
		
		# If no more levels on current page, try to get data from other pages
		if not levels:
			# Try next page first (if we're not on page 0)
			if current_page > 0:
				# Try previous page
				self.page = current_page - 1
				levels, _ = await self.get_page_data()
				
				# If still no levels, try earlier pages
				page_to_try = current_page - 2
				while not levels and page_to_try >= 0:
					self.page = page_to_try
					levels, _ = await self.get_page_data()
					page_to_try -= 1
			
			# If still no levels, try next pages
			if not levels:
				self.page = current_page + 1
				levels, _ = await self.get_page_data()
				
				# If still no levels, keep trying next pages up to 5 more
				page_to_try = current_page + 2
				max_pages_to_try = current_page + 5
				while not levels and page_to_try <= max_pages_to_try:
					self.page = page_to_try
					levels, _ = await self.get_page_data()
					page_to_try += 1
		
		self.update_buttons(levels)
		
		try:
			await self.message.edit(embed=await self.get_embed(), view=self)
		except discord.errors.HTTPException as e:
			# If the message edit fails due to expired token, send a new message
			if e.code == 50027:  # Invalid Webhook Token
				await interaction.followup.send(
					"The review session has been updated. Please use the buttons on this message.",
					view=self,
					embed=await self.get_embed()
				)
				# Update message reference
				self.message = await interaction.followup.fetch_message()
		
	async def on_timeout(self):
		for item in self.children:
			item.disabled = True
		try:
			await self.message.edit(view=self)
		except:
			pass

class CheckLevelView(discord.ui.View):
	def __init__(self, levelData: dict):
		super().__init__(timeout=300)  # 5 minute timeout
		self.message = None
		self.levelData = levelData
		
	@discord.ui.button(label="Suggest Rating", style=discord.ButtonStyle.primary)
	async def suggest_button(self, interaction: discord.Interaction, button: Button):
		# Create a modal for rating input
		class SuggestRatingModal(discord.ui.Modal, title=f"Rate Level: {self.levelData['name']}"):
			difficulty = discord.ui.TextInput(
				label="Difficulty from 1 (Auto) to 10 (Demon)",
				placeholder="Enter a star value from 1 (Auto) to 10 (Demon)",
				required=True,
				min_length=1,
				max_length=2
			)
			
			rating = discord.ui.TextInput(
				label="Rating from 1 (Rate) to 5 (Mythic)",
				placeholder="Enter a star value from 1 (Rate) to 5 (Mythic)",
				required=True,
				min_length=1,
				max_length=1
			)
			
			async def on_submit(self, interaction: discord.Interaction):
				try:
					difficulty_val = int(self.difficulty.value)
					rating_val = int(self.rating.value)
					
					if not (1 <= difficulty_val <= 10) or not (1 <= rating_val <= 5):
						await interaction.response.send_message("Invalid values. Difficulty must be 1-10 and rating must be 1-5.", ephemeral=True)
						return
					
					# Store the user's suggestion
					db.add_user_suggestion(interaction.user.id, self.levelData["id"], difficulty_val, rating_val)
					
					# Get user weight info
					weight_info = db.get_user_weight(interaction.user.id)
					
					await interaction.response.send_message(
						f"Your suggestion for **{self.levelData['name']}** has been recorded.\n\n"
						f"**Difficulty:** {DIFFICULTIES[difficulty_val]}\n"
						f"**Rating:** {RATINGS[rating_val]}\n\n"
						f"Your suggestion weight: **{weight_info['weight']:.2f}**",
						ephemeral=True
					)
				except ValueError:
					await interaction.response.send_message("Please enter valid numbers for difficulty and rating.", ephemeral=True)
				except discord.errors.HTTPException as e:
					# Handle expired token
					if e.code == 50027:  # Invalid Webhook Token
						# Create a new view with a new token
						new_view = CheckLevelView()
						await interaction.followup.send(
							f"Your suggestion for **{self.levelData['name']}** has been recorded, but the session expired. Use this new button if needed.",
							view=new_view,
							ephemeral=True
						)
						new_view.message = await interaction.followup.fetch_message()
		
		try:
			# Send the modal to the user
			modal = SuggestRatingModal()
			await interaction.response.send_modal(modal)
		except discord.errors.HTTPException as e:
			# Handle expired token
			if e.code == 50027:  # Invalid Webhook Token
				# Create a new view and send a new message
				new_view = CheckLevelView()
				embed = discord.Embed(
					title=f"Level: {self.levelData['name']}",
					description=f"The previous view expired. Use this new button to suggest a rating.",
					color=0x00ff00
				)
				await interaction.followup.send(embed=embed, view=new_view, ephemeral=True)
				new_view.message = await interaction.followup.fetch_message()
	
	async def on_timeout(self):
		# Disable the button when the view times out
		if self.message:
			for child in self.children:
				child.disabled = True
			try:
				await self.message.edit(view=self)
			except discord.errors.HTTPException:
				# If edit fails due to token expiry, just pass
				# We can't do much at this point since we can't interact with the user
				pass

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
				async def callback(username: str, player_id: int, account_id: int):
					db.add_creators([{"_id": player_id, "name": username, "accountID": account_id}])
					db.add_follow(interaction.user.id, "creator", player_id)
					await interaction.followup.send(f"✅ Now following **{username}** with ID: `{player_id}`", ephemeral=True)

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
	creator_followers = db.get_followers("creator", level_info["playerID"])

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
			description=f"By **{level['creator']}** ({level['playerID']})\nTotal Sends: **{level['sends']}**\nLevel Info: [GDBrowser](https://gdbrowser.com/{level['_id']}) (`{level['_id']}`)",
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

	# Get suggestion and rating information
	suggestion_data = db.get_weighted_suggestion_average(level_id)
	mod_ratings = db.get_mod_ratings(level_id)
	
	# Create the basic embed
	embed = discord.Embed(
		title=f"{levelData['name']}",
		description=f"{creatorString}Total Sends: **{sendCount}**\nLast Sent: <t:{int(lastSend.timestamp())}:F> (<t:{int(lastSend.timestamp())}:R>)\nLevel Info: [GDBrowser](https://gdbrowser.com/{level_id}) (`{level_id}`)",
		color=0x00ff00 if level_id >= OLDEST_LEVEL else 0xff0000
	)
	
	# Add suggestion data if available
	if suggestion_data["suggestion_count"] > 0:
		embed.add_field(
			name="User Suggestions",
			value=f"**Count:** {suggestion_data['suggestion_count']}\n"
				  f"**Avg Difficulty:** {suggestion_data['difficulty']}/10 ({DIFFICULTIES[suggestion_data['difficulty']]})\n"
				  f"**Avg Rating:** {suggestion_data['rating']}/5 ({RATINGS[suggestion_data['rating']]})",
			inline=True
		)
	
	# Add moderator ratings if available
	if mod_ratings:
		# Count rejections
		rejected_count = sum(1 for r in mod_ratings if r.get("rejected", False))
		approved_ratings = [r for r in mod_ratings if not r.get("rejected", False) and "difficulty" in r and "rating" in r]
		
		if approved_ratings:
			# Calculate averages for non-rejected ratings
			avg_difficulty = sum(r["difficulty"] for r in approved_ratings) / len(approved_ratings)
			avg_rating = sum(r["rating"] for r in approved_ratings) / len(approved_ratings)
			
			rating_text = f"**Count:** {len(mod_ratings)} ({rejected_count} rejections)\n"
			
			if approved_ratings:
				rating_text += f"**Avg Difficulty:** {avg_difficulty:.1f}/10 ({DIFFICULTIES[avg_difficulty]})\n" \
							  f"**Avg Rating:** {avg_rating:.1f}/5 ({RATINGS[avg_rating]})"
			
			if rejected_count > 0:
				rating_text += f"\n**Note:** {rejected_count} moderator{'s' if rejected_count == 1 else 's'} rejected this level"
		else:
			# All ratings are rejections
			rating_text = f"**Note:** All {len(mod_ratings)} moderator{'s' if len(mod_ratings) == 1 else 's'} rejected this level"
			
		embed.add_field(
			name="Moderator Ratings",
			value=rating_text,
			inline=True
		)

	if creatorString:
		embed.set_author(name=levelData["creatorName"], url=f"https://gdbrowser.com/u/{levelData['accountID']}", icon_url="https://gdbrowser.com/assets/cp.png")

	levelData["id"] = level_id

	view = CheckLevelView(levelData)
	await interaction.response.send_message(
		content='⚠️ **WARNING**: This level was created before the bot started tracking levels. The data may be inaccurate.' if level_id < OLDEST_LEVEL else '', 
		embed=embed,
		view=view
	)
	view.message = await interaction.original_response()

@client.tree.command(name="leaderboard", description="Show the send leaderboard.")
async def leaderboard(interaction: discord.Interaction):
	view = LeaderboardView(db, interaction.user.id)
	await interaction.response.send_message(embed=await view.get_embed(), view=view)
	view.message = await interaction.original_response()

@client.tree.command(name="trending", description="Show currently trending levels")
async def trending(interaction: discord.Interaction):
	view = TrendingView(db, interaction.user.id)
	await interaction.response.send_message(embed=await view.get_embed(), view=view)
	view.message = await interaction.original_response()

@client.tree.command(name="info", description="Show the bot's info and stats.")
async def info(interaction: discord.Interaction):
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

@client.tree.command(name="suggest", description="Suggest a difficulty and rating for a level")
@app_commands.describe(
	level_id="The level's ID",
	difficulty="The difficulty level of the level",
	rating="Quality rating for the level"
)
async def suggest_level(
	interaction: discord.Interaction, 
	level_id: int, 
	difficulty: Literal["Auto (1⭐)", "Easy (2⭐)", "Normal (3⭐)", "Hard (4⭐)", "Hard (5⭐)", 
                     "Harder (6⭐)", "Harder (7⭐)", "Insane (8⭐)", "Insane (9⭐)", "Demon (10⭐)"],
	rating: Literal["Rate", "Feature", "Epic", "Legendary", "Mythic"]
):
	# Convert difficulty string to numeric value
	difficulty_value = next((k for k, v in DIFFICULTIES.items() if v == difficulty), 5)
	
	# Convert rating string to numeric value
	rating_value = next((k for k, v in RATINGS.items() if v == rating), 1)
	
	# Store the user's suggestion
	db.add_user_suggestion(interaction.user.id, level_id, difficulty_value, rating_value)
	
	# Get info about the level
	infoData = db.get_info([level_id])
	if level_id not in infoData:
		level_name = f"Level {level_id}"
		creator_name = "Unknown"
	else:
		level_info = infoData[level_id]
		level_name = level_info["name"]
		
		creator_data = db.get_creators([level_info["creator"]])
		if level_info["creator"] in creator_data:
			creator_name = creator_data[level_info["creator"]]["name"]
		else:
			creator_name = "Unknown"
	
	# Get the user's weight
	weight_info = db.get_user_weight(interaction.user.id)
	
	# Create embed response
	embed = discord.Embed(
		title=f"Suggestion Recorded",
		description=f"Your suggestion for **{level_name}** by **{creator_name}** has been recorded:\n\n"
				   f"**Difficulty:** {difficulty}\n"
				   f"**Rating:** {rating}\n\n"
				   f"Your suggestion weight: **{weight_info['weight']:.2f}**\n"
				   f"Your accuracy score: **{weight_info['accuracy'] * 100:.1f}%**\n"
				   f"Total suggestions: **{weight_info['suggestion_count']}**",
		color=0x00ff00
	)
	
	# Add information about what the weights mean
	embed.add_field(
		name="About Suggestion Weights",
		value="Your suggestion weight is based on how closely your past suggestions matched moderator ratings. "
			  "The more accurate your suggestions, the higher your weight will be in future calculations.",
		inline=False
	)
	
	await interaction.response.send_message(embed=embed, ephemeral=True)

@client.tree.command(name="pending-suggestions", description="View levels with pending user suggestions")
@app_commands.check(is_moderator)
async def pending_suggestions(interaction: discord.Interaction):
	view = ModReviewView(db, interaction.user.id)
	levels, _ = await view.get_page_data()
	view.update_buttons(levels)
	await interaction.response.send_message(embed=await view.get_embed(), view=view, ephemeral=True)
	view.message = await interaction.original_response()

@client.tree.command(name="my-suggestions", description="View your suggestion history and accuracy")
async def my_suggestions(interaction: discord.Interaction):
	# Get user weight information
	weight_info = db.get_user_weight(interaction.user.id)
	
	# Get user suggestions
	suggestions = list(db.get_collection("data", "user_suggestions").find({"user_id": interaction.user.id}).sort("timestamp", -1).limit(10))
	
	embed = discord.Embed(
		title="Your Suggestion Profile",
		description=f"**Weight:** {weight_info.get('weight', 1.0):.2f}\n"
				   f"**Accuracy:** {weight_info.get('accuracy', 0.0) * 100:.1f}%\n"
				   f"**Total Suggestions:** {weight_info.get('suggestion_count', 0)}",
		color=0x00ff00
	)
	
	if suggestions:
		# Get level info for all suggestions
		level_ids = [s["level_id"] for s in suggestions]
		level_info = db.get_info(level_ids)
		
		# Add recent suggestions
		for i, s in enumerate(suggestions[:5]):
			level_id = s["level_id"]
			level_name = level_info.get(level_id, {}).get("name", f"Level {level_id}")
			
			processed = "✅" if s.get("processed_by_mod", False) else "⏳"
			
			embed.add_field(
				name=f"Suggestion #{i+1}: {level_name}",
				value=f"**Difficulty:** {DIFFICULTIES[s['difficulty']]}\n"
					  f"**Rating:** {RATINGS[s['rating']]}\n"
					  f"**Processed:** {processed}\n"
					  f"**When:** <t:{int(s['timestamp'].timestamp())}:R>",
				inline=True
			)
	else:
		embed.add_field(
			name="No Suggestions",
			value="You haven't made any suggestions yet. Use `/suggest` to rate levels.",
			inline=False
		)
	
	await interaction.response.send_message(embed=embed, ephemeral=True)

client.run(environ.get("BOT_TOKEN"))