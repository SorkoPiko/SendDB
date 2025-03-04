from typing import Tuple, List, Any, Mapping

from pymongo import UpdateOne
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime, UTC, timedelta

class SendDB:
	def __init__(self, connection_string: str):
		self.client = MongoClient(connection_string, server_api=ServerApi('1'))
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
		creators = self.get_collection("data", "creators")
		return list(creators.find(
			{"name": {"$regex": f"^{query}", "$options": "i"}},
			{"_id": 1, "name": 1, "accountID": 1}
		).limit(25))

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
	
	def add_mod_rating(self, mod_id: int, level_id: int, difficulty: int, rating: int):
		"""Add a moderator's rating for a level"""
		mod_ratings = self.get_collection("data", "mod_ratings")
		
		# Create or update mod rating
		mod_ratings.update_one(
			{"mod_id": mod_id, "level_id": level_id},
			{"$set": {
				"difficulty": difficulty,
				"rating": rating,
				"timestamp": datetime.now(UTC)
			}},
			upsert=True
		)
		
		# Mark all user suggestions for this level as processed
		suggestions = self.get_collection("data", "user_suggestions")
		suggestions.update_many(
			{"level_id": level_id, "processed_by_mod": False},
			{"$set": {"processed_by_mod": True}}
		)
		
		# Update user weights based on how close their suggestions were
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
		
		# Get all user suggestions for this level
		user_suggestions = list(suggestions.find({"level_id": level_id, "processed_by_mod": True}))
		
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
			
			# Update the user's weight information
			weights.update_one(
				{"user_id": user_id},
				{"$inc": {
					"suggestion_count": 1,
					"correct_suggestions": accuracy
				}},
				upsert=True
			)
			
			# Recalculate the overall weight
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
									0.5,  # Base weight
									{"$multiply": [1.5, "$accuracy"]}  # Increases with accuracy up to 1.5 additional weight
								]
							}
						}
					}
				]
			)
	
	def get_pending_suggestions(self, page: int = 0, limit: int = 10) -> tuple[list[dict], int]:
		"""Get levels with pending user suggestions that need moderator review"""
		suggestions = self.get_collection("data", "user_suggestions")
		
		# Find levels that have unprocessed suggestions
		pipeline = [
			{"$match": {"processed_by_mod": False}},
			{"$group": {
				"_id": "$level_id",
				"suggestion_count": {"$sum": 1},
				"latest_suggestion": {"$max": "$timestamp"}
			}},
			{"$sort": {"latest_suggestion": -1}},
			{"$facet": {
				"total": [{"$count": "count"}],
				"data": [
					{"$skip": page * limit},
					{"$limit": limit},
					{
						"$lookup": {
							"from": "info",
							"localField": "_id",
							"foreignField": "_id",
							"as": "level_info"
						}
					},
					{"$unwind": {"path": "$level_info", "preserveNullAndEmptyArrays": True}},
					{
						"$lookup": {
							"from": "creators",
							"localField": "level_info.creator",
							"foreignField": "_id",
							"as": "creator_info"
						}
					},
					{"$unwind": {"path": "$creator_info", "preserveNullAndEmptyArrays": True}},
					{
						"$project": {
							"level_id": "$_id",
							"level_name": {"$ifNull": ["$level_info.name", "Unknown"]},
							"creator_id": {"$ifNull": ["$level_info.creator", 0]},
							"creator_name": {"$ifNull": ["$creator_info.name", "Unknown"]},
							"suggestion_count": 1,
							"latest_suggestion": 1
						}
					}
				]
			}}
		]
		
		result = list(suggestions.aggregate(pipeline))
		if not result or not result[0]["total"]:
			return [], 0
			
		return result[0]["data"], result[0]["total"][0]["count"]
	
	def get_weighted_suggestion_average(self, level_id: int) -> dict:
		"""Calculate weighted average of user suggestions for a level"""
		suggestions = self.get_collection("data", "user_suggestions")
		
		# Get all user suggestions with their weights
		user_suggestions = self.get_user_suggestions(level_id)
		
		if not user_suggestions:
			return {"difficulty": 0, "rating": 0, "suggestion_count": 0}
		
		total_weight = 0
		weighted_difficulty_sum = 0
		weighted_rating_sum = 0
		
		for suggestion in user_suggestions:
			weight = suggestion.get("weight", 1.0)
			total_weight += weight
			weighted_difficulty_sum += suggestion["difficulty"] * weight
			weighted_rating_sum += suggestion["rating"] * weight
		
		if total_weight == 0:
			return {"difficulty": 0, "rating": 0, "suggestion_count": len(user_suggestions)}
		
		return {
			"difficulty": round(weighted_difficulty_sum / total_weight, 1),
			"rating": round(weighted_rating_sum / total_weight, 1),
			"suggestion_count": len(user_suggestions)
		}

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
		"""Get all moderators"""
		moderators = self.get_collection("data", "moderators")
		return list(moderators.find().sort("username", 1))