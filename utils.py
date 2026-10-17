import logging

import requests, threading, time, asyncio, queue, math
from datetime import datetime, UTC
from typing import Optional, Callable
from db import SendDB

DEMON_MAP = {
	3: 0,
	4: 1,
	0: 2,
	5: 3,
	6: 4
}

HYPED_PAGE_DELAY = 2.5
HYPED_RETRIES = 3
HYPED_RETRY_DELAY = 5
HYPED_FALLBACK_TIME = 70*60
HYPED_SCAN_ATTEMPTS = 3

class Ratelimited(Exception):
	pass

class Banned(Exception):
	pass

class InvalidResponse(Exception):
	pass

class SentChecker:
	def __init__(self, callback: Callable, ban_callback: Optional[Callable] = None, db: Optional[SendDB] = None):
		self.q: queue.Queue = queue.Queue()
		self.pending_checks: dict[str, list[Callable]] = {}
		self.callback = callback
		self.ban_callback = ban_callback
		self.lock = threading.Lock()
		self.loop: Optional[asyncio.AbstractEventLoop] = None
		self.thread: Optional[threading.Thread] = None
		self.running = threading.Event()
		self.db = db
		self.hyped_baseline: Optional[list[int]] = None
		self.last_hyped_time = 0.0
		self.hyped_total = 0

	def start(self, loop: asyncio.AbstractEventLoop):
		"""Start the worker thread with the given event loop"""
		self.loop = loop
		self.running.set()
		self.thread = threading.Thread(target=self.worker)
		self.thread.daemon = True
		self.thread.start()

	def stop(self):
		"""Stop the worker thread"""
		if self.running.is_set():
			self.running.clear()
			self.q.put((None, None))
			if self.thread:
				self.thread.join(timeout=5)

	def worker(self):
		while self.running.is_set():
			try:
				username = self.q.get(timeout=1)
			except queue.Empty:
				username = None

			try:
				with self.lock:
					if not self.running.is_set():
						break

					if username:
						callbacks = self.pending_checks.pop(username, [])
						username, player_id, account_id = self.check_account(username)
						self.db.increase_stat("requests", 1)
						for callback, args, _ in callbacks:
							if self.running.is_set() and self.loop and not self.loop.is_closed():
								self.loop.call_soon_threadsafe(
									lambda: asyncio.create_task(callback(username, player_id, account_id, *args))
								)

					time.sleep(3)

					rated_levels, rated_creators = self.getRatedLevels()
					self.db.increase_stat("requests", 1)
					if self.running.is_set() and self.loop and not self.loop.is_closed():
						self.loop.call_soon_threadsafe(
							lambda: asyncio.create_task(self.callback(rated_levels, rated_creators))
						)
					time.sleep(5)

				if username:
					self.q.task_done()

				# outside the lock so a full scan doesn't block queue_check on the event loop
				if self.running.is_set():
					self.checkHyped()
					time.sleep(3)

			except Ratelimited:
				time.sleep(60*60)
			except Banned:
				if self.ban_callback:
					asyncio.run_coroutine_threadsafe(self.ban_callback(), self.loop)
				break
			except Exception as e:
				logging.error(f"Error in worker thread: {e}", exc_info=True)
				time.sleep(10)

	@staticmethod
	def getLevelsPage(levelType: int, page: int = 0, total: int = 0, hypeSecret: bool = False, diff: str = "-") -> tuple[list[dict], list[dict], int, int]:
		data = {
			"type": levelType,
			"str": "",
			"diff": diff,
			"len": "-",
			"page": page,
			"total": total,
			"uncompleted": 0,
			"onlyCompleted": 0,
			"featured": 0,
			"original": 0,
			"twoPlayer": 0,
			"coins": 0,
			"secret": "Wmfd2893gb7",
			"gameVersion": 22,
			"binaryVersion": 51
		}
		if hypeSecret:
			data["hypeSecret"] = 1

		headers = {
			"User-Agent": ""
		}

		req = requests.post('http://www.boomlings.com/database/getGJLevels21.php', data=data, headers=headers)
		SentChecker.check_errors(req)

		if req.status_code != 200 or req.text == "-1":
			raise InvalidResponse(f"HTTP {req.status_code}: {req.text[:100]}")

		try:
			parsed = req.text.split("#")
			rawLevels = parsed[0].split("|")
			rawCreators = parsed[1].split("|") if parsed[1] else []
			pageInfo = parsed[3].split(":")

			levels = []

			for level in rawLevels:
				parts = level.split(":")
				data = {parts[i]: parts[i + 1] for i in range(0, len(parts), 2)}
				_15 = int(data.get("15", 0))
				levels.append({
					"_id": int(data["1"]),
					"name": data["2"],
					"creatorID": int(data["6"]),
					"length": _15,
					"platformer": (_15 == 5)
				})

			creators = []

			for creator in rawCreators:
				parts = creator.split(":")
				try:
					accountID = int(parts[2])
				except Exception:
					accountID = 0
				creators.append({
					"_id": int(parts[0]),
					"name": parts[1],
					"accountID": accountID
				})

			return levels, creators, int(pageInfo[0]), int(pageInfo[2])
		except Exception as e:
			raise InvalidResponse(f"Unparseable response ({e}): {req.text[:100]}")

	@staticmethod
	def getHypedLevels(page: int = 0, total: int = 0, diff: str = "-") -> tuple[list[dict], list[dict], int, int]:
		return SentChecker.getLevelsPage(31, page, total, hypeSecret=True, diff=diff)

	def fetchWithRetries(self, fetch: Callable, *args):
		"""Fetch a levels page, retrying failed or non-level responses."""
		for attempt in range(HYPED_RETRIES + 1):
			try:
				self.db.increase_stat("requests", 1)
				return fetch(*args)
			except (Ratelimited, Banned):
				raise
			except Exception as e:
				logging.warning(f"Levels page fetch failed (attempt {attempt + 1}/{HYPED_RETRIES + 1}): {e}")
				if attempt < HYPED_RETRIES:
					time.sleep(HYPED_RETRY_DELAY)
		return None

	def checkHyped(self):
		"""Fetch page 0 of the hyped list and run a full scan if it changed."""
		page0 = self.fetchWithRetries(self.getHypedLevels, 0)
		if page0 is None:
			return

		page0IDs = [level["_id"] for level in page0[0]]
		total = page0[2]
		if self.hyped_baseline is not None and time.monotonic() - self.last_hyped_time < HYPED_FALLBACK_TIME:
			removed = self.getRemovedLevels(self.hyped_baseline, page0IDs)
			if removed is not None:
				if total != self.hyped_total - len(removed):
					# levels joined or left somewhere past page 0
					logging.info(f"Hyped total changed from {self.hyped_total} to {total}, rescanning")
					self.scanHyped(page0, allowUnchangedOrder=True)
					return

				# levels leave the list when rated or expired, which isn't a reshuffle
				if removed:
					logging.info(f"Levels left the hyped list: {removed}")
					self.hyped_baseline = [level for level in self.hyped_baseline if level not in removed]
					self.hyped_total = total
					self.addHypedEvent(datetime.now(UTC), removed, [], "page0")
				return

		self.scanHyped(page0)

	def addHypedEvent(self, timestamp: datetime, removed: list[int], added: list[int], source: str):
		"""Record levels joining or leaving the hyped list."""
		self.db.add_hyped_event({
			"timestamp": timestamp,
			"removed": removed,
			"added": added,
			"source": source
		})
		print(f"Hyped list change ({source}): {len(removed)} left, {len(added)} joined")

	@staticmethod
	def getRemovedLevels(baseline: list[int], page0IDs: list[int]) -> Optional[list[int]]:
		"""Return the baseline levels missing from page 0, or None if page 0 has new or reordered levels."""
		removed = []
		index = 0
		for levelID in page0IDs:
			while index < len(baseline) and baseline[index] != levelID:
				removed.append(baseline[index])
				index += 1
			if index == len(baseline):
				return None
			index += 1
		return removed

	def scanHyped(self, page0: tuple[list[dict], list[dict], int, int], allowUnchangedOrder: bool = False):
		"""Page through the whole hyped list and store it as a snapshot, rescanning if it moved mid-scan.

		With allowUnchangedOrder, a complete scan that only removed levels records an event instead of a snapshot."""
		timestamp = datetime.now(UTC)

		scan = None
		for attempt in range(HYPED_SCAN_ATTEMPTS):
			if page0 is None:
				time.sleep(HYPED_PAGE_DELAY)
				page0 = self.fetchWithRetries(self.getHypedLevels, 0)
				if page0 is None:
					break

			result = self.scanHypedPages(page0)
			if result is None:
				return
			scan, page0 = result[:5], result[5]

			if not scan[4]:
				break
			logging.warning(f"Hyped list moved during scan (attempt {attempt + 1}/{HYPED_SCAN_ATTEMPTS})")

		if scan is None:
			return
		levels, creators, total, failedPages, moved = scan

		levelIDs = [level["_id"] for level in levels]
		complete = not failedPages and not moved

		if complete and self.hyped_baseline is not None:
			current = set(levelIDs)
			previous = set(self.hyped_baseline)
			removed = [level for level in self.hyped_baseline if level not in current]
			added = [level for level in levelIDs if level not in previous]

			if allowUnchangedOrder and not added and self.getRemovedLevels(self.hyped_baseline, levelIDs) is not None:
				if removed:
					self.addHypedEvent(timestamp, removed, [], "total")
				self.hyped_baseline = levelIDs.copy()
				self.hyped_total = total
				return

			if removed or added:
				self.addHypedEvent(timestamp, removed, added, "snapshot")

		demon = self.scanHypedDemons(levelIDs)
		if demon is None:
			return
		demonIDs, demonComplete = demon

		snapshot = {
			"timestamp": timestamp,
			"levels": levelIDs,
			"total": total,
			"complete": complete,
			"failed_pages": failedPages,
			"demon": demonIDs,
			"demon_complete": demonComplete
		}

		self.db.add_hyped_snapshot(snapshot)
		self.db.add_info([{"_id": level["_id"], "name": level["name"], "creator": level["creatorID"], "length": level["length"], "platformer": level["platformer"]} for level in levels])
		self.db.add_creators(list({creator["_id"]: creator for creator in creators}.values()))

		self.hyped_baseline = levelIDs.copy()
		self.hyped_total = total
		self.last_hyped_time = time.monotonic()
		print(f"Stored hyped snapshot: {len(levelIDs)}/{total} levels, {len(demonIDs)} sent for demon, failed pages: {failedPages}, moved: {moved}")

	def scanHypedDemons(self, levelIDs: list[int]) -> Optional[tuple[list[int], bool]]:
		"""Scan the demon-filtered hyped list, which holds the levels sent for demon."""
		time.sleep(HYPED_PAGE_DELAY)
		page0 = self.fetchWithRetries(self.getHypedLevels, 0, 0, "-2")
		if page0 is None:
			# an empty result is also -1, so this can mean no demon sends
			return [], False

		result = self.scanHypedPages(page0, "-2")
		if result is None:
			return None
		levels, _, total, failedPages, moved, _ = result

		demonIDs = [level["_id"] for level in levels]
		outside = set(demonIDs) - set(levelIDs)
		if outside:
			logging.warning(f"{len(outside)} demon-filtered levels aren't in the hyped snapshot")
		return demonIDs, not failedPages and not moved and not outside

	def scanHypedPages(self, page0: tuple[list[dict], list[dict], int, int], diff: str = "-"):
		"""Fetch every page after page 0, then re-fetch page 0 to check whether the list moved."""
		levels, creators, total, pageSize = page0
		page0IDs = [level["_id"] for level in levels]
		failedPages = []
		moved = False

		for page in range(1, math.ceil(total / pageSize) if pageSize else 0):
			if not self.running.is_set():
				return None
			time.sleep(HYPED_PAGE_DELAY)

			result = self.fetchWithRetries(self.getHypedLevels, page, total, diff)
			if result is None:
				failedPages.append(page)
				continue

			if result[2] != total:
				logging.warning(f"Hyped page {page} reports total {result[2]}, page 0 reported {total}")
				moved = True
			levels = levels + result[0]
			creators = creators + result[1]

		levelIDs = [level["_id"] for level in levels]
		if not failedPages and len(levelIDs) != total:
			logging.warning(f"Hyped scan collected {len(levelIDs)} levels, total is {total}")
			moved = True
		if len(set(levelIDs)) != len(levelIDs):
			logging.warning(f"Hyped scan has {len(levelIDs) - len(set(levelIDs))} duplicate levels")
			moved = True

		time.sleep(HYPED_PAGE_DELAY)
		newPage0 = self.fetchWithRetries(self.getHypedLevels, 0, 0, diff)
		if newPage0 is not None and [level["_id"] for level in newPage0[0]] != page0IDs:
			logging.warning("Hyped page 0 changed during scan")
			moved = True

		return levels, creators, total, failedPages, moved, newPage0

	@staticmethod
	def getRatedLevels() -> tuple[list[dict], list[dict]]:
		data = {
			"type": 11,  # rated levels type
			"secret": "Wmfd2893gb7"
		}

		headers = {
			"User-Agent": ""
		}

		req = requests.post('http://www.boomlings.com/database/getGJLevels21.php', data=data, headers=headers)
		SentChecker.check_errors(req)

		if req.text == "-1": return [], []

		parsed = req.text.split("#")
		rawLevels = parsed[0].split("|")
		rawCreators = parsed[1].split("|")

		levels = []

		for level in rawLevels:
			parts = level.split(":")
			data = {parts[i]: parts[i + 1] for i in range(0, len(parts), 2)}
			_15 = int(data.get("15", 0))
			_18 = int(data.get("18", 0))
			_42 = int(data.get("42", 0))
			levels.append({
				"_id": int(data["1"]),
				"name": data["2"],
				"creatorID": int(data["6"]),
				"length": _15,
				"platformer": (_15 == 5),
				"difficulty": DEMON_MAP.get(int(data.get("43", 3)), 0),
				"stars": _18,
				"points": (1 if _18 > 0 else 0) + (1 if int(data.get("19", 0)) > 0 else 0) + (_42 if _42 > 0 else 0) # https://github.com/RedLime/DemonObserver/blob/1.2-master/src/classes/demon.js#L4
			})

		creators = []

		for creator in rawCreators:
			parts = creator.split(":")
			creators.append({
				"_id": int(parts[0]),
				"name": parts[1],
				"accountID": int(parts[2])
			})

		return levels, creators

	@staticmethod
	def check_account(username) -> tuple[str, int, int]:
		data = {
			"str": username,
			"secret": "Wmfd2893gb7"
		}

		headers = {
			"User-Agent": ""
		}

		req = requests.post('http://www.boomlings.com/database/getGJUsers20.php', data=data, headers=headers)

		if req.text == "-1": return "", 0, 0

		split = req.text.split(":")
		pairs = {int(split[i]): split[i+1] for i in range(0, len(split), 2)}

		return pairs[1], int(pairs[2]), int(pairs[16])

	def queue_check(self, username: str, callback: Callable, user: int, *args):
		"""Queue a username for checking, avoiding duplicates."""
		with self.lock:
			if username in self.pending_checks:
				# Add the callback to the existing list
				self.pending_checks[username].append((callback, args, user))
			else:
				# Add a new entry and queue the username
				self.pending_checks[username] = [(callback, args, user)]
				self.q.put(username)

	def is_user_pending(self, checkUser: int):
		"""Check if a user is already pending."""
		with self.lock:
			for callbacks in self.pending_checks.values():
				for _, _, user in callbacks:
					if user == checkUser:
						return True
		return False

	def approximate_wait_time(self, user: int) -> float:
		"""Estimate the approximate wait time based on the number of pending checks."""
		waitTime = 5
		with self.lock:
			for username, callbacks in self.pending_checks.items():
				for _, _, checkUser in callbacks:
					if checkUser == user:
						break
				waitTime += 10

		return waitTime

	@staticmethod
	def check_errors(req):
		if "error code: 1015" in req.text: # ratelimited
			logging.warning("Ratelimited!")
			raise Ratelimited()

		if "error code: 1005" in req.text: # asn ban
			logging.warning("ASN Banned!")
			raise Banned()

		if "error code: 1006" in req.text: # ip ban
			logging.warning("IP Banned!")
			raise Banned()