import re
from typing import Optional
from pymongo import UpdateOne
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi, ServerApiVersion
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime, UTC, timedelta

class SendDB:
	def __init__(self, connection_string: str):
		self.client = MongoClient(connection_string, server_api=ServerApi(ServerApiVersion.V1))
		self.create_indexes()

	def create_indexes(self):
		follows = self.get_collection("data", "follows")
		follows.create_index([("user_id", 1), ("type", 1), ("followed_id", 1)], unique=True)

		# Create indexes for user suggestions
		suggestions = self.get_collection("data", "user_suggestions")
		suggestions.create_index([("user_id", 1), ("level_id", 1)], unique=True)
		suggestions.create_index("level_id")
		suggestions.create_index("processed_by_mod")
		suggestions.create_index("timestamp")

		# Create indexes for mod ratings
		mod_ratings = self.get_collection("data", "mod_ratings")
		mod_ratings.create_index([("mod_id", 1), ("level_id", 1)], unique=True)
		mod_ratings.create_index("level_id")

		# Create indexes for user weights
		weights = self.get_collection("data", "user_weights")
		weights.create_index("user_id", unique=True)
		weights.create_index("weight")

		# Create indexes for moderators
		moderators = self.get_collection("data", "moderators")
		moderators.create_index("discord_id", unique=True)
		moderators.create_index("username")

	def get_database(self, db_name: str) -> Database:
		return self.client[db_name]

	def get_collection(self, db_name: str, collection_name: str) -> Collection:
		db = self.get_database(db_name)
		return db[collection_name]

	def add_sends(self, sends: list[dict]):
		if not sends: return

		sends_collection = self.get_collection("data", "sends")
		sends_collection.insert_many(sends)

	def add_info(self, info: list[dict]):
		if not info: return

		info_collection = self.get_collection("data", "info")
		operations = [
			UpdateOne(
				{"_id": item["_id"]},
				{"$set": item},
				upsert=True
			) for item in info
		]
		info_collection.bulk_write(operations)

	def add_creators(self, creators: list[dict]):
		if not creators: return

		creators_collection = self.get_collection("data", "creators")
		operations = [
			UpdateOne(
				{"_id": creator["_id"]},
				{"$set": creator},
				upsert=True
			) for creator in creators
		]
		creators_collection.bulk_write(operations)

	def add_rates(self, rates: list[dict]):
		if not rates: return

		rates_collection = self.get_collection("data", "rates")
		operations = [
			UpdateOne(
				{"_id": rate["_id"]},
				{
					"$setOnInsert": {
						"timestamp": rate.get("timestamp", datetime.now(UTC))
					},
					"$set": {k: v for k, v in rate.items() if k != "_id" and k != "timestamp"}
				},
				upsert=True
			)
			for rate in rates
		]
		rates_collection.bulk_write(operations, ordered=False)

	def remove_rates(self, ids: list[int]):
		if not ids: return

		rates_collection = self.get_collection("data", "rates")
		rates_collection.delete_many({"_id": {"$in": ids}})

	def set_mod(self, id: int, timestamp: datetime, mod: int):
		sends = self.get_collection("data", "sends")
		sends.update_one({"_id": id, "timestamp": timestamp}, {"$set": {"mod": mod}})

	def get_sends(self, level_ids: list[int]) -> dict:
		sends = self.get_collection("data", "sends")
		pipeline = [
			{"$match": {"levelID": {"$in": level_ids}}},
			{"$group": {"_id": "$levelID", "count": {"$sum": 1}, "latest_timestamp": {"$max": "$timestamp"}}}
		]
		results = sends.aggregate(pipeline)
		return {result["_id"]: {"count": result["count"], "latest_timestamp": result["latest_timestamp"]} for result in results}

	def get_creators(self, creator_ids: list[int]) -> dict:
		creators = self.get_collection("data", "creators")
		pipeline = [
			{"$match": {"_id": {"$in": creator_ids}}},
			{"$project": {"_id": 1, "name": 1, "accountID": 1}}
		]
		results = creators.aggregate(pipeline)
		return {result["_id"]: {"name": result["name"], "accountID": result["accountID"]} for result in results}


	def get_creator_info(self, creator_id: int) -> dict:
		info = self.get_collection("data", "info")
		sends = self.get_collection("data", "sends")
		follows = self.get_collection("data", "follows")

		info_pipeline = [
			{"$match": {"creator": creator_id}},
			{
				"$group": {
					"_id": None,
					"level_ids": {"$push": "$_id"},
					"level_count": {"$sum": 1},
					"creator_id": {"$first": "$creator"}
				}
			},
			{
				"$lookup": {
					"from": "creators",
					"localField": "creator_id",
					"foreignField": "_id",
					"as": "creator_info"
				}
			},
			{
				"$project": {
					"level_ids": 1,
					"level_count": 1,
					"creator_info": {"$arrayElemAt": ["$creator_info", 0]}
				}
			}
		]

		info_result = list(info.aggregate(info_pipeline))

		if not info_result:
			return {}

		info_data = info_result[0]
		level_ids = info_data["level_ids"]
		level_count = info_data["level_count"]
		creator_info = info_data["creator_info"]

		sends_pipeline = [
			{"$match": {"levelID": {"$in": level_ids}}},
			{
				"$group": {
					"_id": None,
					"sends_count": {"$sum": 1},
					"latest_send": {"$max": "$timestamp"}
				}
			}
		]
		sends_result = list(sends.aggregate(sends_pipeline))

		followers_count = follows.count_documents({"type": "creator", "followed_id": creator_id})

		return {
			"userID": creator_id,
			"name": creator_info["name"],
			"accountID": creator_info["accountID"],
			"sends_count": sends_result[0]["sends_count"] if sends_result else 0,
			"latest_send": sends_result[0]["latest_send"] if sends_result else None,
			"level_count": level_count,
			"followers_count": followers_count
		}

	def get_info(self, level_ids: list[int]) -> dict:
		info = self.get_collection("data", "info")
		pipeline = [
			{"$match": {"_id": {"$in": level_ids}}},
			{"$project": {"_id": 1, "name": 1, "creator": 1}}
		]
		results = info.aggregate(pipeline)
		return {result["_id"]: {"name": result["name"], "creator": result["creator"]} for result in results}

	def raw_pipeline(self, collection: str, pipeline: list[dict]):
		collection = self.get_collection("data", collection)

		return list(collection.aggregate(pipeline))

	def get_total_sends(self):
		sends = self.get_collection("data", "sends")
		return sends.count_documents({})

	def get_total_creators(self):
		creators = self.get_collection("data", "creators")
		return creators.count_documents({})

	def get_total_levels(self):
		info = self.get_collection("data", "info")
		return info.count_documents({})

	def get_oldest_level(self):
		info = self.get_collection("data", "info")
		return info.find_one(sort=[("_id", 1)])

	def get_oldest_creator(self):
		creators = self.get_collection("data", "creators")
		return creators.find_one(sort=[("_id", 1)])

	def get_latest_send(self):
		sends = self.get_collection("data", "sends")
		return sends.find_one(sort=[("timestamp", -1)])

	def add_follow(self, user_id: int, followed_type: str, followed_id: int):
		follows = self.get_collection("data", "follows")
		follows.update_one(
			{"user_id": user_id, "type": followed_type, "followed_id": followed_id},
			{"$set": {"timestamp": datetime.now(UTC)}},
			upsert=True
		)

	def remove_follow(self, user_id: int, followed_type: str, followed_id: int):
		follows = self.get_collection("data", "follows")
		follows.delete_one({"user_id": user_id, "type": followed_type, "followed_id": followed_id})

	def get_follows(self, user_id: int) -> list[dict]:
		follows = self.get_collection("data", "follows")
		return list(follows.find({"user_id": user_id}))

	def get_followers(self, followed_type: str, followed_id: int) -> list[int]:
		follows = self.get_collection("data", "follows")
		results = follows.find({"type": followed_type, "followed_id": followed_id})
		return [result["user_id"] for result in results]

	def search_creators(self, query: str) -> list[dict]:
		query = re.escape(query)
		creators = self.get_collection("data", "creators")
		return list(creators.find(
			{"name": {"$regex": f"^{query}", "$options": "i"}},
			{"_id": 1, "name": 1, "accountID": 1}
		).limit(25))

	def search_levels(self, query: str) -> list[dict]:
		"""
		Search for levels by name.

		Args:
			query: The search term to look for in level names

		Returns:
			list: A list of matching level dictionaries with id and name
		"""

		query = re.escape(query)

		levels = self.get_collection("data", "info")
		return list(levels.find(
			{"name": {"$regex": f"{query}", "$options": "i"}},
			{"_id": 1, "name": 1}
		).sort("name", 1).limit(25))

	def get_trending_levels(self, skip: int = 0, limit: int = 10, get_total: bool = False) -> tuple[list[dict], int]:
		sends = self.get_collection("data", "sends")

		base_pipeline = [
			{
				"$match": {
					"timestamp": {
						"$gte": datetime.now(UTC) - timedelta(days=30)
					}
				}
			},
			{
				"$addFields": {
					"age_hours": {
						"$divide": [
							{"$subtract": [datetime.now(UTC), "$timestamp"]},
							1000 * 60 * 60
						]
					}
				}
			},
			{
				"$group": {
					"_id": "$levelID",
					"score": {
						"$sum": {
							"$multiply": [
								25000,
								{
									"$divide": [
										1,
										{"$pow": [
											{"$add": [{"$divide": ["$age_hours", 24]}, 2]},
											1
										]}
									]
								}
							]
						}
					},
					"recent_sends": {"$sum": 1},
					"latest_send": {"$max": "$timestamp"}
				}
			},
			{
				"$lookup": {
					"from": "rates",
					"localField": "_id",
					"foreignField": "_id",
					"as": "rate"
				}
			},
			{
				"$match": {
					"rate": {"$size": 0}
				}
			}
		]

		if get_total:
			pipeline = base_pipeline + [
				{"$facet": {
					"total": [{"$count": "count"}],
					"data": [
						{
							"$lookup": {
								"from": "info",
								"localField": "_id",
								"foreignField": "_id",
								"as": "level_info"
							}
						},
						{"$unwind": "$level_info"},
						{
							"$lookup": {
								"from": "creators",
								"localField": "level_info.creator",
								"foreignField": "_id",
								"as": "creator_info"
							}
						},
						{"$unwind": "$creator_info"},
						{
							"$project": {
								"name": "$level_info.name",
								"levelID": "$_id",
								"creator": "$creator_info.name",
								"creatorID": "$creator_info._id",
								"score": 1,
								"recent_sends": 1,
								"latest_send": 1
							}
						},
						{"$sort": {"score": -1}},
						{"$skip": skip},
						{"$limit": limit}
					]
				}}
			]

			result = list(sends.aggregate(pipeline))
			if not result or not result[0]["total"]:
				return [], 0

			return result[0]["data"], result[0]["total"][0]["count"]
		else:
			pipeline = base_pipeline + [
				{
					"$lookup": {
						"from": "info",
						"localField": "_id",
						"foreignField": "_id",
						"as": "level_info"
					}
				},
				{"$unwind": "$level_info"},
				{
					"$lookup": {
						"from": "creators",
						"localField": "level_info.creator",
						"foreignField": "_id",
						"as": "creator_info"
					}
				},
				{"$unwind": "$creator_info"},
				{
					"$project": {
						"name": "$level_info.name",
						"levelID": "$_id",
						"creator": "$creator_info.name",
						"creatorID": "$creator_info._id",
						"score": 1,
						"recent_sends": 1,
						"latest_send": 1
					}
				},
				{"$sort": {"score": -1}},
				{"$limit": limit}
			]

			return list(sends.aggregate(pipeline)), None

	# User suggestion methods
	def add_user_suggestion(self, user_id: int, level_id: int, difficulty: int, rating: int):
		"""Add a user's suggestion for a level's difficulty and rating"""
		suggestions = self.get_collection("data", "user_suggestions")

		# Create or update suggestion
		suggestions.update_one(
			{"user_id": user_id, "level_id": level_id},
			{"$set": {
				"difficulty": difficulty,
				"rating": rating,
				"timestamp": datetime.now(UTC),
				"processed_by_mod": False
			}},
			upsert=True
		)

	def get_user_suggestions(self, level_id: int) -> list[dict]:
		"""Get all user suggestions for a level"""
		suggestions = self.get_collection("data", "user_suggestions")
		user_suggestions = list(suggestions.find({"level_id": level_id}))

		# Get user weights to include with suggestions
		if user_suggestions:
			user_ids = [s["user_id"] for s in user_suggestions]
			weights = {w["user_id"]: w for w in self.get_user_weights(user_ids)}

			# Add weight information to suggestions
			for suggestion in user_suggestions:
				user_id = suggestion["user_id"]
				if user_id in weights:
					suggestion["weight"] = weights[user_id].get("weight", 1.0)
					suggestion["suggestion_count"] = weights[user_id].get("suggestion_count", 0)
					suggestion["accuracy"] = weights[user_id].get("accuracy", 0.0)
				else:
					suggestion["weight"] = 1.0
					suggestion["suggestion_count"] = 0
					suggestion["accuracy"] = 0.0

		return user_suggestions

	def add_mod_rating(self, mod_id: int, level_id: int, difficulty: int = None, rating: int = None, rejected: bool = False):
		"""Add a moderator's rating for a level"""
		mod_ratings = self.get_collection("data", "mod_ratings")

		# Create rating data with required fields
		rating_data = {
			"timestamp": datetime.now(UTC),
			"rejected": rejected
		}

		# Add optional fields if provided
		if difficulty is not None:
			rating_data["difficulty"] = difficulty

		if rating is not None:
			rating_data["rating"] = rating

		# Create or update mod rating
		mod_ratings.update_one(
			{"mod_id": mod_id, "level_id": level_id},
			{"$set": rating_data},
			upsert=True
		)

		# Mark suggestions for this level as processed by this moderator
		suggestions = self.get_collection("data", "user_suggestions")
		suggestions.update_many(
			{"level_id": level_id},
			{"$set": {"processed_by_mod": True}}
		)

		# Update user weights based on how close their suggestions were
		if rejected:
			self._update_user_weights_for_rejected(level_id)
		elif difficulty is not None and rating is not None:
			self._update_user_weights(level_id, difficulty, rating)

	def get_mod_ratings(self, level_id: int) -> list[dict]:
		"""Get all moderator ratings for a level"""
		mod_ratings = self.get_collection("data", "mod_ratings")
		return list(mod_ratings.find({"level_id": level_id}))

	def get_user_weight(self, user_id: int) -> dict:
		"""Get a user's weight information"""
		weights = self.get_collection("data", "user_weights")
		weight_info = weights.find_one({"user_id": user_id})

		if not weight_info:
			# Return default weight info
			return {
				"user_id": user_id,
				"weight": 1.0,
				"suggestion_count": 0,
				"correct_suggestions": 0,
				"accuracy": 0.0
			}

		return weight_info

	def get_user_weights(self, user_ids: list[int]) -> list[dict]:
		"""Get weight information for multiple users"""
		weights = self.get_collection("data", "user_weights")
		return list(weights.find({"user_id": {"$in": user_ids}}))

	def _update_user_weights(self, level_id: int, mod_difficulty: int, mod_rating: int):
		"""Update user weights based on how close their suggestions were to moderator ratings"""
		suggestions = self.get_collection("data", "user_suggestions")
		weights = self.get_collection("data", "user_weights")

		# Get all user suggestions for this level (no longer filtering by processed_by_mod)
		user_suggestions = list(suggestions.find({"level_id": level_id}))

		for suggestion in user_suggestions:
			user_id = suggestion["user_id"]
			user_difficulty = suggestion["difficulty"]
			user_rating = suggestion["rating"]

			# Calculate accuracy based on how close the user's suggestion was
			# Difficulty is on a scale of 1-10, rating is on a scale of 1-5
			# Normalize the difference for each scale
			difficulty_diff = abs(user_difficulty - mod_difficulty) / 9  # 9 is max possible difference (1 to 10)
			rating_diff = abs(user_rating - mod_rating) / 4  # 4 is max possible difference (1 to 5)

			# Average the normalized differences and convert to accuracy (0-1)
			accuracy = 1 - ((difficulty_diff + rating_diff) / 2)

			# Apply asymmetric weighting:
			# - Accurate suggestions (>0.7) get full credit
			# - Moderately accurate suggestions (0.4-0.7) get slightly reduced credit
			# - Inaccurate suggestions (<0.4) are penalized more heavily
			weighted_accuracy = accuracy
			if accuracy < 0.4:
				# For very inaccurate suggestions, apply a stronger penalty
				# This makes a bad suggestion worth less than its raw accuracy
				weighted_accuracy = accuracy * 0.5  # Reduce value by 50%
			elif accuracy < 0.7:
				# For moderately accurate suggestions, apply a small penalty
				weighted_accuracy = accuracy * 0.8  # Reduce value by 20%

			# Update the user's weight information
			weights.update_one(
				{"user_id": user_id},
				{"$inc": {
					"suggestion_count": 1,
					"correct_suggestions": weighted_accuracy
				}},
				upsert=True
			)

			# Recalculate the overall weight using a gradual growth formula
			# that rewards long-term participation with accurate suggestions
			weights.update_one(
				{"user_id": user_id},
				[
					{
						"$set": {
							"accuracy": {
								"$cond": {
									"if": {"$gt": ["$suggestion_count", 0]},
									"then": {"$divide": ["$correct_suggestions", "$suggestion_count"]},
									"else": 0
								}
							}
						}
					},
					{
						"$set": {
							"weight": {
								"$add": [
									1.0,  # Base weight (minimum)
									{
										"$multiply": [
											9.0,  # Maximum bonus weight (making max total 10.0)
											"$accuracy",
											{
												"$divide": [
													"$suggestion_count",
													{"$add": ["$suggestion_count", 20]}  # Damping factor
												]
											}
										]
									}
								]
							}
						}
					}
				]
			)

	def get_pending_suggestions(self, page: int = 0, page_size: int = 10, mod_id: Optional[int] = None):
		"""
		Retrieves a paginated list of levels that have user suggestions but haven't been reviewed
		by the specified moderator.

		Args:
			page: Page number (0-indexed)
			page_size: Number of results per page
			mod_id: If provided, filter out levels this moderator has already rated

		Returns:
			tuple: (list of level dicts, total count)
		"""
		# Collection references
		suggestions = self.get_collection("data", "user_suggestions")

		# If mod_id is provided, filter out levels this moderator has already rated
		already_rated = []
		if mod_id is not None:
			mod_ratings = self.get_collection("data", "mod_ratings").find({"mod_id": mod_id})
			for rating in mod_ratings:
				already_rated.append(rating["level_id"])

		# Base match criteria - get unique level IDs with suggestions
		match_filter = {}
		if already_rated:
			match_filter["level_id"] = {"$nin": already_rated}

		# Get distinct level IDs with suggestions
		pipeline = [
			{"$match": match_filter},
			{"$group": {
				"_id": "$level_id",
				"suggestion_count": {"$sum": 1},
				"latest_suggestion": {"$max": "$timestamp"}
			}},
			{"$sort": {"latest_suggestion": -1}},
			{"$facet": {
				"paginatedResults": [
					{"$skip": page * page_size},
					{"$limit": page_size}
				],
				"totalCount": [{"$count": "count"}]
			}}
		]

		results = list(suggestions.aggregate(pipeline))

		levels = []
		total_count = 0

		if results and results[0]["paginatedResults"]:
			paginated_results = results[0]["paginatedResults"]
			level_ids = [result["_id"] for result in paginated_results]

			# Get level info
			level_info = self.get_info(level_ids)

			# Get creator info for these levels
			creator_ids = [info["creator"] for lid, info in level_info.items() if "creator" in info]
			creators = self.get_creators(creator_ids)

			for result in paginated_results:
				level_id = result["_id"]
				level_data = {
					"level_id": level_id,
					"level_name": level_info.get(level_id, {}).get("name", f"Level {level_id}"),
					"creator_name": "Unknown",
					"suggestion_count": result["suggestion_count"],
					"latest_suggestion": result["latest_suggestion"]
				}

				# Add creator info if available
				if level_id in level_info and "creator" in level_info[level_id]:
					creator_id = level_info[level_id]["creator"]
					if creator_id in creators:
						level_data["creator_name"] = creators[creator_id].get("name", "Unknown")

				levels.append(level_data)

			if results[0]["totalCount"]:
				total_count = results[0]["totalCount"][0]["count"]

		return levels, total_count

	def get_weighted_suggestion_average(self, level_id: int) -> dict:
		"""Calculate weighted average of user suggestions for a level"""
		suggestions = self.get_collection("data", "user_suggestions")

		# Get all user suggestions with their weights
		user_suggestions = self.get_user_suggestions(level_id)

		if not user_suggestions:
			return {"difficulty": 0, "rating": 0, "suggestion_count": 0}

		# Check if the level has been rejected by any moderator
		mod_ratings = self.get_mod_ratings(level_id)
		rejection_count = sum(1 for r in mod_ratings if r.get("rejected", False))
		total_mod_ratings = len(mod_ratings)

		# Include rejection information in the result
		result = {
			"suggestion_count": len(user_suggestions),
			"mod_count": total_mod_ratings,
			"rejection_count": rejection_count
		}

		if rejection_count == total_mod_ratings and total_mod_ratings > 0:
			# All moderators rejected this level
			result["difficulty"] = 0
			result["rating"] = 0
			result["all_rejected"] = True
			return result

		total_weight = 0
		weighted_difficulty_sum = 0
		weighted_rating_sum = 0

		for suggestion in user_suggestions:
			weight = suggestion.get("weight", 1.0)
			total_weight += weight
			weighted_difficulty_sum += suggestion["difficulty"] * weight
			weighted_rating_sum += suggestion["rating"] * weight

		if total_weight == 0:
			result["difficulty"] = 0
			result["rating"] = 0
			return result

		result["difficulty"] = round(weighted_difficulty_sum / total_weight, 1)
		result["rating"] = round(weighted_rating_sum / total_weight, 1)

		return result

	def get_suggestion_score(self, level_id: int) -> float:
		"""Calculate a suggestion score based on the combined weights of all suggesters for a level"""
		suggestions = self.get_collection("data", "user_suggestions")

		# Get all user suggestions with their weights
		user_suggestions = self.get_user_suggestions(level_id)

		if not user_suggestions:
			return 0.0

		# Sum all weights to get a suggestion score
		total_weight = sum(suggestion.get("weight", 1.0) for suggestion in user_suggestions)

		# Scale the score to make it more meaningful
		# This gives levels with many high-weight users higher scores
		return round(total_weight, 1)

	# Moderator management methods
	def add_moderator(self, discord_id: int, username: str) -> bool:
		"""Add a moderator to the database"""
		moderators = self.get_collection("data", "moderators")

		try:
			moderators.update_one(
				{"discord_id": discord_id},
				{"$set": {
					"discord_id": discord_id,
					"username": username,
					"added_at": datetime.now(UTC)
				}},
				upsert=True
			)
			return True
		except Exception as e:
			print(f"Error adding moderator: {e}")
			return False

	def remove_moderator(self, discord_id: int) -> bool:
		"""Remove a moderator from the database"""
		moderators = self.get_collection("data", "moderators")

		try:
			result = moderators.delete_one({"discord_id": discord_id})
			return result.deleted_count > 0
		except Exception as e:
			print(f"Error removing moderator: {e}")
			return False

	def is_moderator(self, discord_id: int) -> bool:
		"""Check if a user is a moderator"""
		moderators = self.get_collection("data", "moderators")
		return moderators.count_documents({"discord_id": discord_id}) > 0

	def get_moderator(self, discord_id: int) -> dict:
		"""Get moderator info by discord ID"""
		moderators = self.get_collection("data", "moderators")
		return moderators.find_one({"discord_id": discord_id})

	def get_all_moderators(self) -> list[dict]:
		"""Get all moderators from the database."""
		mods = self.get_collection("data", "moderators").find({})
		return list(mods)

	def get_pending_suggestion_count(self, mod_id: Optional[int] = None) -> int:
		"""
		Get the total count of levels that have pending user suggestions.

		Args:
			mod_id: If provided, only count levels this specific moderator hasn't rated yet

		Returns:
			int: The count of levels with pending suggestions
		"""
		# Collection references
		suggestions = self.get_collection("data", "user_suggestions")

		# Get level IDs that have already been rated
		already_rated = []
		if mod_id is not None:
			# Only filter out levels this specific moderator has rated
			mod_ratings = self.get_collection("data", "mod_ratings").find({"mod_id": mod_id})
			for rating in mod_ratings:
				already_rated.append(rating["level_id"])
		else:
			# Filter out levels that any moderator has rated
			mod_ratings = self.get_collection("data", "mod_ratings").find({}, {"level_id": 1})
			for rating in mod_ratings:
				already_rated.append(rating["level_id"])

		# Base match criteria - get unique level IDs with suggestions
		match_filter = {}
		if already_rated:
			match_filter["level_id"] = {"$nin": already_rated}

		# Count unique level IDs with suggestions that match our filter
		pipeline = [
			{"$match": match_filter},
			{"$group": {"_id": "$level_id"}},
			{"$count": "pending_count"}
		]

		result = list(suggestions.aggregate(pipeline))

		if result and "pending_count" in result[0]:
			return result[0]["pending_count"]
		return 0

	def _update_user_weights_for_rejected(self, level_id: int):
		"""Penalize users who suggested ratings for a level that was rejected by a moderator"""
		suggestions = self.get_collection("data", "user_suggestions")
		weights = self.get_collection("data", "user_weights")

		# Get all user suggestions for this level (no longer filtering by processed_by_mod)
		user_suggestions = list(suggestions.find({"level_id": level_id}))

		for suggestion in user_suggestions:
			user_id = suggestion["user_id"]

			# Apply a stronger penalty for rejected levels
			# Instead of just adding 0 to correct_suggestions (which would be neutral),
			# we'll actually subtract from their total correct_suggestions as a penalty
			penalty = -0.5  # This effectively counts as NEGATIVE half a suggestion

			# Update the user's weight information
			weights.update_one(
				{"user_id": user_id},
				{"$inc": {
					"suggestion_count": 1,
					"correct_suggestions": penalty  # Apply negative points for suggesting rejected levels
				}},
				upsert=True
			)

			# Recalculate the overall weight using the same gradual growth formula
			weights.update_one(
				{"user_id": user_id},
				[
					{
						"$set": {
							"accuracy": {
								"$cond": {
									"if": {"$gt": ["$suggestion_count", 0]},
									"then": {"$divide": ["$correct_suggestions", "$suggestion_count"]},
									"else": 0
								}
							}
						}
					},
					{
						"$set": {
							"weight": {
								"$add": [
									1.0,  # Base weight (minimum)
									{
										"$multiply": [
											9.0,  # Maximum bonus weight (making max total 10.0)
											"$accuracy",
											{
												"$divide": [
													"$suggestion_count",
													{"$add": ["$suggestion_count", 20]}  # Damping factor
												]
											}
										]
									}
								]
							}
						}
					}
				]
			)

	def get_moderator_position(self, mod_id: int) -> int:
		"""
		Determine a moderator's position/ranking compared to other moderators
		based on how many levels they've reviewed.

		Args:
			mod_id: The Discord ID of the moderator

		Returns:
			int: The moderator's position (1-indexed, where 1 is the most active)
				 Returns 0 if the moderator hasn't reviewed any levels or isn't found.
		"""
		mod_ratings = self.get_collection("data", "mod_ratings")

		# Get the count of unique levels reviewed by each moderator
		pipeline = [
			{"$group": {
				"_id": {
					"mod_id": "$mod_id",
					"level_id": "$level_id"
				}
			}},
			{"$group": {
				"_id": "$_id.mod_id",
				"level_count": {"$sum": 1}
			}},
			{"$sort": {"level_count": -1}}
		]

		results = list(mod_ratings.aggregate(pipeline))

		# If no results, return 0
		if not results:
			return 0

		# Look for the target moderator in the results
		for i, result in enumerate(results):
			if result["_id"] == mod_id:
				return i + 1  # Convert to 1-indexed position

		# If moderator not found in results, they haven't reviewed any levels
		return 0

	def set_stat(self, stat: str, value: int):
		stats = self.get_collection("data", "stats")
		stats.update_one({"_id": stat}, {"$set": {"value": value}}, upsert=True)

	def increase_stat(self, stat: str, amount: int = 1):
		stats = self.get_collection("data", "stats")
		stats.update_one({"_id": stat}, {"$inc": {"value": amount}}, upsert=True)

	def get_stat(self, stat: str) -> int:
		stats = self.get_collection("data", "stats")
		return stats.find_one({"_id": stat})["value"]