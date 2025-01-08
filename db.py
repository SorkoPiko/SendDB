from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
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

    def add_send(self, id: int, timestamp: datetime, mod: int = None):
        sends = self.get_collection("data", "sends")

        sendDict = {
            "_id": id,
            "timestamp": timestamp
        }

        if mod:
            sendDict["mod"] = mod

        try:
            sends.insert_one(sendDict)
        except DuplicateKeyError:
            pass

    def add_sends(self, sends: list[dict]):
        if not sends: return

        sends = self.get_collection("data", "sends")
        sends.insert_many(sends)

    def add_creator(self, id: int, name: str):
        creators = self.get_collection("data", "creators")
        creators.insert_one({"_id": id, "name": name})

    def add_creators(self, creators: list[dict]):
        if not creators: return

        creators = self.get_collection("data", "creators")
        creators.insert_many(creators)

    def set_mod(self, id: int, timestamp: datetime, mod: int):
        sends = self.get_collection("data", "sends")
        sends.update_one({"_id": id, "timestamp": timestamp}, {"$set": {"mod": mod}})

    def get_sends(self, id: int) -> dict:
        sends = self.get_collection("data", "sends")
        sendsRaw = sends.find({"_id": id})
        sends = {}
        for send in sendsRaw:
            sendDict = {
                "timestamp": send["timestamp"]
            }

            if "mod" in send:
                sendDict["mod"] = send["mod"]

            sends[send["_id"]] = sendDict

        return sends