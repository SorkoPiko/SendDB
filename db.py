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