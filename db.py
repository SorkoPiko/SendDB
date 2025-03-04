from typing import Tuple, List, Any, Mapping

from pymongo import UpdateOne
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime, UTC, timedelta
from bson.objectid import ObjectId

class SendDB:
    def __init__(self, connection_string: str):
        self.client = MongoClient(connection_string, server_api=ServerApi('1'))
        self.create_indexes()

    def create_indexes(self):
        follows = self.get_collection("data", "follows")
        follows.create_index([("user_id", 1), ("type", 1), ("followed_id", 1)], unique=True)

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

    def add_suggestion(self, level_id: int, user_id: int, difficulty: int, rating: int):
        """Add a new suggestion for a level's difficulty and rating."""
        suggestions = self.get_collection("data", "suggestions")
        suggestions.insert_one({
            "levelID": level_id,
            "userID": user_id,
            "difficulty": difficulty,
            "rating": rating,
            "timestamp": datetime.now(UTC),
            "status": "pending"
        })

    def get_pending_suggestions(self, level_id: int = None, skip: int = 0, limit: int = 10, get_total: bool = False, moderator_id: int = None) -> tuple[list[dict], int]:
        """Get pending suggestions, optionally filtered by level ID and excluding those already moderated by the given moderator."""
        suggestions = self.get_collection("data", "suggestions")
        user_scores = self.get_collection("data", "user_scores")

        match_stage = {}
        if level_id is not None:
            match_stage["levelID"] = level_id
        if moderator_id is not None:
            # Only show suggestions this moderator hasn't handled
            match_stage["moderatedBy"] = {"$ne": moderator_id}

        base_pipeline = [
            {"$match": match_stage},
            {
                "$lookup": {
                    "from": "user_scores",
                    "localField": "userID",
                    "foreignField": "_id",
                    "as": "user_score"
                }
            },
            {"$unwind": {"path": "$user_score", "preserveNullAndEmptyArrays": True}},
            {
                "$addFields": {
                    "weighted_score": {"$ifNull": ["$user_score.weighted_score", 0]}
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
                                "localField": "levelID",
                                "foreignField": "_id",
                                "as": "level_info"
                            }
                        },
                        {
                            "$project": {
                                "levelID": 1,
                                "userID": 1,
                                "difficulty": 1,
                                "rating": 1,
                                "timestamp": 1,
                                "weighted_score": 1,
                                "level_name": {"$ifNull": [{"$first": "$level_info.name"}, "Unknown"]},
                                "level_creator": {"$ifNull": [{"$first": "$level_info.creator"}, None]}
                            }
                        },
                        {"$sort": {"weighted_score": -1, "timestamp": 1}},
                        {"$skip": skip},
                        {"$limit": limit}
                    ]
                }}
            ]

            result = list(suggestions.aggregate(pipeline))
            if not result or not result[0]["total"]:
                return [], 0

            # Get creator names for the results
            data = result[0]["data"]
            creator_ids = [item["level_creator"] for item in data if item["level_creator"] is not None]
            creators = self.get_creators(creator_ids)

            for item in data:
                if item["level_creator"] in creators:
                    item["creator_name"] = creators[item["level_creator"]]["name"]
                else:
                    item["creator_name"] = "Unknown"

            return data, result[0]["total"][0]["count"]
        else:
            pipeline = base_pipeline + [
                {
                    "$lookup": {
                        "from": "info",
                        "localField": "levelID",
                        "foreignField": "_id",
                        "as": "level_info"
                    }
                },
                {
                    "$project": {
                        "levelID": 1,
                        "userID": 1,
                        "difficulty": 1,
                        "rating": 1,
                        "timestamp": 1,
                        "weighted_score": 1,
                        "level_name": {"$ifNull": [{"$first": "$level_info.name"}, "Unknown"]},
                        "level_creator": {"$ifNull": [{"$first": "$level_info.creator"}, None]}
                    }
                },
                {"$sort": {"weighted_score": -1, "timestamp": 1}},
                {"$limit": limit}
            ]

            data = list(suggestions.aggregate(pipeline))
            
            # Get creator names for the results
            creator_ids = [item["level_creator"] for item in data if item["level_creator"] is not None]
            creators = self.get_creators(creator_ids)

            for item in data:
                if item["level_creator"] in creators:
                    item["creator_name"] = creators[item["level_creator"]]["name"]
                else:
                    item["creator_name"] = "Unknown"

            return data, None

    def moderate_suggestion(self, suggestion_id: str, moderator_id: int, is_sent: bool, difficulty: int = None, rating: int = None):
        """Mark a suggestion as moderated with a moderator's decision."""
        suggestions = self.get_collection("data", "suggestions")
        
        decision = {
            "moderatorID": moderator_id,
            "timestamp": datetime.now(UTC),
            "is_sent": is_sent
        }
        
        if is_sent and difficulty is not None and rating is not None:
            decision.update({
                "difficulty": difficulty,
                "rating": rating
            })
        
        # Add the decision to the decisions array and mark as seen by this moderator
        update = {
            "$push": {
                "decisions": decision
            },
            "$addToSet": {
                "moderatedBy": moderator_id
            }
        }
        
        suggestions.update_one(
            {"_id": ObjectId(suggestion_id)},
            update
        )
        
        # Update user score
        suggestion = suggestions.find_one({"_id": ObjectId(suggestion_id)})
        if suggestion:
            self._update_user_score(suggestion["userID"])

    def get_user_suggestions(self, user_id: int, skip: int = 0, limit: int = 10, get_total: bool = False) -> tuple[list[dict], int]:
        """Get a user's suggestions with pagination."""
        suggestions = self.get_collection("data", "suggestions")
        
        base_pipeline = [
            {"$match": {"userID": user_id}},
            {
                "$lookup": {
                    "from": "info",
                    "localField": "levelID",
                    "foreignField": "_id",
                    "as": "level_info"
                }
            }
        ]

        if get_total:
            pipeline = base_pipeline + [
                {"$facet": {
                    "total": [{"$count": "count"}],
                    "data": [
                        {
                            "$project": {
                                "levelID": 1,
                                "difficulty": 1,
                                "rating": 1,
                                "timestamp": 1,
                                "status": 1,
                                "level_name": {"$ifNull": [{"$first": "$level_info.name"}, "Unknown"]},
                                "level_creator": {"$ifNull": [{"$first": "$level_info.creator"}, None]}
                            }
                        },
                        {"$sort": {"timestamp": -1}},
                        {"$skip": skip},
                        {"$limit": limit}
                    ]
                }}
            ]

            result = list(suggestions.aggregate(pipeline))
            if not result or not result[0]["total"]:
                return [], 0

            # Get creator names for the results
            data = result[0]["data"]
            creator_ids = [item["level_creator"] for item in data if item["level_creator"] is not None]
            creators = self.get_creators(creator_ids)

            for item in data:
                if item["level_creator"] in creators:
                    item["creator_name"] = creators[item["level_creator"]]["name"]
                else:
                    item["creator_name"] = "Unknown"

            return data, result[0]["total"][0]["count"]
        else:
            pipeline = base_pipeline + [
                {
                    "$project": {
                        "levelID": 1,
                        "difficulty": 1,
                        "rating": 1,
                        "timestamp": 1,
                        "status": 1,
                        "level_name": {"$ifNull": [{"$first": "$level_info.name"}, "Unknown"]},
                        "level_creator": {"$ifNull": [{"$first": "$level_info.creator"}, None]}
                    }
                },
                {"$sort": {"timestamp": -1}},
                {"$limit": limit}
            ]

            data = list(suggestions.aggregate(pipeline))
            
            # Get creator names for the results
            creator_ids = [item["level_creator"] for item in data if item["level_creator"] is not None]
            creators = self.get_creators(creator_ids)

            for item in data:
                if item["level_creator"] in creators:
                    item["creator_name"] = creators[item["level_creator"]]["name"]
                else:
                    item["creator_name"] = "Unknown"

            return data, None

    def _update_user_score(self, user_id: int):
        """Update a user's suggestion score based on moderator decisions."""
        suggestions = self.get_collection("data", "suggestions")
        user_scores = self.get_collection("data", "user_scores")

        # Get all suggestions by this user that have at least one decision
        user_suggestions = list(suggestions.find({
            "userID": user_id,
            "decisions": {"$exists": True, "$ne": []}
        }))
        
        if not user_suggestions:
            return

        total_decisions = 0
        approved_decisions = 0
        
        # Count decisions from all moderators
        for suggestion in user_suggestions:
            for decision in suggestion.get("decisions", []):
                total_decisions += 1
                if decision["is_sent"]:
                    approved_decisions += 1
        
        # Calculate accuracy based on all moderator decisions
        accuracy = (approved_decisions / total_decisions) if total_decisions > 0 else 0
        
        # Update user scores
        user_scores.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "total_suggestions": len(user_suggestions),
                    "total_moderator_decisions": total_decisions,
                    "approved_decisions": approved_decisions,
                    "accuracy_score": accuracy * 100,  # Store as percentage
                    "weighted_score": accuracy * 100,  # Store as percentage
                    "last_updated": datetime.now(UTC)
                }
            },
            upsert=True
        )

    def get_user_score(self, user_id: int) -> dict:
        """Get a user's suggestion score."""
        user_scores = self.get_collection("data", "user_scores")
        return user_scores.find_one({"_id": user_id}) or {
            "_id": user_id,
            "total_suggestions": 0,
            "total_moderator_decisions": 0,
            "approved_decisions": 0,
            "accuracy_score": 0,
            "weighted_score": 0,
            "last_updated": None
        }

    def get_top_suggesters(self, limit: int = 10) -> list[dict]:
        """Get the top users by weighted score."""
        user_scores = self.get_collection("data", "user_scores")
        return list(user_scores.find(
            {"total_suggestions": {"$gt": 0}},
            sort=[("weighted_score", -1)],
            limit=limit
        ))

    def apply_for_moderator(self, discord_id: int, gd_username: str) -> bool:
        """Apply to become a moderator."""
        moderators = self.get_collection("data", "moderators")
        
        # Check if already applied or is moderator
        existing = moderators.find_one({"discord_id": discord_id})
        if existing:
            return False
            
        moderators.insert_one({
            "discord_id": discord_id,
            "gd_username": gd_username,
            "status": "pending",
            "applied_at": datetime.now(UTC)
        })
        return True

    def is_moderator(self, discord_id: int) -> bool:
        """Check if a user is a moderator."""
        moderators = self.get_collection("data", "moderators")
        return moderators.find_one({"discord_id": discord_id, "status": "approved"}) is not None