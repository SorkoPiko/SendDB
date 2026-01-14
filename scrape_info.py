from datetime import datetime
from os import environ

import requests
from dotenv import load_dotenv
from pymongo import UpdateOne
from tqdm import tqdm

import utils
from db import SendDB

load_dotenv()
connection_string = environ.get('MONGO_CONNECTION_STRING')
if connection_string is None:
	raise EnvironmentError("MONGO_CONNECTION_STRING environment variable is not set.")

db = SendDB(connection_string)

info_collection = db.get_collection("data", "info")
rate_collection = db.get_collection("data", "rates")
pipeline = [
	{
		"$lookup": {
			"from": "rates",
			"localField": "_id",
			"foreignField": "_id",
			"as": "rate_data"
		}
	},
	{
		"$match": {
			"$or": [
				{"length": {"$exists": False}},
				{"platformer": {"$exists": False}},
				{"rate_data": {"$size": 0}}
			]
		}
	},
	{
		"$project": {
			"_id": 1
		}
	}
]

cursor = info_collection.aggregate(pipeline)
ids = [doc["_id"] for doc in cursor]

info_operations = []
rate_operations = []
for id in tqdm(ids, desc="Scraping levels", unit="level"):
	response = requests.get(
		f'https://history.geometrydash.eu/api/v1/level/{id}'
	)

	if response.status_code != 200:
		print(f"Failed to fetch data for ID {id}: {response.status_code}")
		continue

	data = response.json()

	if data['online_id'] != id:
		print(f"Data mismatch for ID {id}: received online_id {data['online_id']}")
		continue

	# info
	length = data.get('cache_length', 0)
	update_fields = {
		'length': length,
		'platformer': (length == 5)
	}
	info_operations.append(
		UpdateOne(
			{'_id': id},
			{'$set': update_fields}
		)
	)

	# rates
	stars = data.get("cache_stars", 0)
	points = min(stars, 1) + min(data.get("cache_featured", 0), 1) + data.get("cache_epic", 0)
	if points > 0:
		records = sorted(data.get("records", []), key=lambda r: r.get("real_date", ""))

		latest = records[-1]
		demon = utils.DEMON_MAP.get(latest.get("demon_type", 3), 0)

		for record in records:
			if (record.get("stars", 0) or 0) <= 0: continue

			timestamp = datetime.fromisoformat(record["real_date"].replace("Z", "+00:00"))
			rate_operations.append(
				UpdateOne(
					{'_id': id},
					{
						'$setOnInsert': {
							'timestamp': timestamp,
							'accurate': False
						},
						'$set': {
							'difficulty': demon,
							'stars': stars,
							'points': points
						}
					},
					upsert=True
				)
			)
			break

	if len(info_operations) > 100:
		info_collection.bulk_write(info_operations)
		info_operations = []

	if len(rate_operations) > 100:
		rate_collection.bulk_write(rate_operations)
		rate_operations = []

if info_operations:
	info_collection.bulk_write(info_operations)
if rate_operations:
	rate_collection.bulk_write(rate_operations)