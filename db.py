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
                            "$divide": [1, {"$add": ["$age_hours", 1]}]
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