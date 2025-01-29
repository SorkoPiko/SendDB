from pymongo import UpdateOne
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.collection import Collection
from pymongo.database import Database
from datetime import datetime

class SendDB:
    def __init__(self, connection_string: str):
        self.client = MongoClient(connection_string, server_api=ServerApi('1'))
        # self.create_indexes()

    # def create_indexes(self):
    #     sends = self.get_collection("data", "sends")
    #     sends.create_index([("ip", ASCENDING), ("levelID", ASCENDING)], unique=True)
    #
    #     demon = self.get_collection("data", "demon")
    #     demon.create_index([("ip", ASCENDING), ("levelID", ASCENDING)], unique=True)

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

    def get_leaderboard(self, skip: int = 0, limit: int = 10) -> tuple[list[dict], int]:
        """
        Get paginated leaderboard data sorted by number of sends using a single database query

        Args:
            skip (int): Number of documents to skip
            limit (int): Number of documents to return

        Returns:
            tuple[list[dict], int]: List of leaderboard entries and total count
        """
        sends = self.get_collection("data", "sends")

        pipeline = [
            # First group by levelID to count sends per level
            {"$group": {
                "_id": "$levelID",
                "count": {"$sum": 1}
            }},
            # Join with level info to get creator IDs
            {"$lookup": {
                "from": "info",
                "localField": "_id",
                "foreignField": "_id",
                "as": "level_info"
            }},
            {"$unwind": "$level_info"},
            # Group by creator to sum all their level sends
            {"$group": {
                "_id": "$level_info.creator",
                "sends": {"$sum": "$count"}
            }},
            # Use $facet to run both the count and paginated data in parallel
            {"$facet": {
                "total": [
                    {"$count": "count"}
                ],
                "data": [
                    # Join with creators to get names and account IDs
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
                        "sends": 1
                    }},
                    {"$sort": {"sends": -1}},
                    {"$skip": skip},
                    {"$limit": limit}
                ]
            }}
        ]

        result = list(sends.aggregate(pipeline))

        if not result or not result[0]["total"]:
            return [], 0

        return result[0]["data"], result[0]["total"][0]["count"]