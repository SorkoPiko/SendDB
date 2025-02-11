import requests, threading, time, asyncio, queue
from typing import Optional, Callable

class Ratelimited(Exception):
    pass

class Banned(Exception):
    pass

class SentChecker:
    def __init__(self, callback: Callable, ban_callback: Optional[Callable] = None):
        self.q: queue.Queue = queue.Queue()
        self.callback = callback
        self.ban_callback = ban_callback
        self.lock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.running = threading.Event()

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
                username, callback, *args = self.q.get(timeout=1)
            except queue.Empty:
                username, callback, *args = None, None, []

            try:
                with self.lock:
                    if not self.running.is_set():
                        break

                    levels, creators = self.getSentLevels()

                    time.sleep(2)

                    if username:
                        player_id, account_id = self.check_account(username)
                        if self.running.is_set() and self.loop and not self.loop.is_closed():
                            self.loop.call_soon_threadsafe(
                                lambda: asyncio.create_task(callback(player_id, account_id, *args))
                            )

                    time.sleep(3)

                    rated_levels = self.getRatedLevels()
                    if self.running.is_set() and self.loop and not self.loop.is_closed():
                        self.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self.callback(levels, creators, rated_levels))
                        )
                    time.sleep(5)

                self.q.task_done()
            except Ratelimited:
                time.sleep(60*60)
            except Banned:
                if self.ban_callback:
                    self.ban_callback()
                break
            except Exception as e:
                print(f"Error in worker thread: {e}")
                time.sleep(10)

    @staticmethod
    def getSentLevels() -> tuple[list[dict], list[dict]]:
        data = {
            "type": 27,  # new sent levels type
            "secret": "Wmfd2893gb7"
        }

        headers = {
            "User-Agent": ""
        }

        req = requests.post('http://www.boomlings.com/database/getGJLevels21.php', data=data, headers=headers)

        if req.text == "error code: 1015": # ratelimited
            print("Ratelimited!")
            raise Ratelimited()

        if req.text == "error code: 1005": # asn ban
            print("ASN Banned!")
            raise Banned()

        if req.text == "error code: 1006": # ip ban
            print("IP Banned!")
            raise Banned()

        if req.text == "-1": return [], []

        parsed = req.text.split("#")
        rawLevels = parsed[0].split("|")
        rawCreators = parsed[1].split("|")

        levels = []

        for level in rawLevels:
            parts = level.split(":")
            data = {parts[i]: parts[i + 1] for i in range(0, len(parts), 2)}
            levels.append({
                "_id": int(data["1"]),
                "name": data["2"],
                "creatorID": int(data["6"])
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
    def getRatedLevels() -> list[int]:
        data = {
            "type": 11,  # rated levels type
            "secret": "Wmfd2893gb7"
        }

        headers = {
            "User-Agent": ""
        }

        req = requests.post('http://www.boomlings.com/database/getGJLevels21.php', data=data, headers=headers)

        if req.text == "-1": return []

        parsed = req.text.split("#")
        rawLevels = parsed[0].split("|")

        rated_level_ids = []
        for level in rawLevels:
            parts = level.split(":")
            data = {parts[i]: parts[i + 1] for i in range(0, len(parts), 2)}
            rated_level_ids.append(int(data["1"]))

        return rated_level_ids

    @staticmethod
    def check_account(username) -> [int, int]:
        data = {
            "str": username,
            "secret": "Wmfd2893gb7"
        }

        headers = {
            "User-Agent": ""
        }

        req = requests.post('http://www.boomlings.com/database/getGJUsers20.php', data=data, headers=headers)

        if req.text == "-1": return 0

        split = req.text.split(":")
        pairs = {int(split[i]): split[i+1] for i in range(0, len(split), 2)}

        return int(pairs[2]), int(pairs[16])

    def queue_check(self, username, callback, *args):
        if self.running.is_set():
            self.q.put((username, callback, *args))