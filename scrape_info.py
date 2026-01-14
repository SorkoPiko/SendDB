import asyncio
from datetime import datetime
from os import environ

import aiohttp
from dotenv import load_dotenv
from pymongo import UpdateOne
from tqdm.asyncio import tqdm

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

async def fetch_level(session, level_id, semaphore):
	async with semaphore:
		try:
			async with session.get(f'https://history.geometrydash.eu/api/v1/level/{level_id}') as response:
				if response.status != 200:
					print(f"Failed to fetch data for ID {level_id}: {response.status}")
					return None
				data = await response.json()
				if data['online_id'] != level_id:
					print(f"Data mismatch for ID {level_id}: received online_id {data['online_id']}")
					return None
				return level_id, data
		except Exception as e:
			print(f"Error fetching ID {level_id}: {e}")
			return None

async def process_levels():
	info_operations = []
	rate_operations = []

	semaphore = asyncio.Semaphore(20)

	async with aiohttp.ClientSession() as session:
		tasks = [fetch_level(session, level_id, semaphore) for level_id in ids]

		for coro in tqdm.as_completed(tasks, desc="Scraping levels", unit="level", total=len(ids)):
			result = await coro
			if result is None:
				continue

			level_id, data = result

			# info
			length = data.get('cache_length', 0)
			update_fields = {
				'length': length,
				'platformer': (length == 5)
			}
			info_operations.append(
				UpdateOne(
					{'_id': level_id},
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
					if (record.get("stars", 0) or 0) <= 0:
						continue

					timestamp = datetime.fromisoformat(record["real_date"].replace("Z", "+00:00"))
					rate_operations.append(
						UpdateOne(
							{'_id': level_id},
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

asyncio.run(process_levels())