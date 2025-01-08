import requests
import threading
import time
import asyncio
from typing import Optional, Callable

class SentChecker:
    def __init__(self, callback: Callable):
        self.callback = callback
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
            if self.thread:
                self.thread.join(timeout=5)

    def worker(self):
        while self.running.is_set():
            try:
                with self.lock:
                    if not self.running.is_set():
                        break

                    levels, creators = self.getSentLevels()
                    if self.running.is_set() and self.loop and not self.loop.is_closed():
                        self.loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self.callback(levels, creators))
                        )
                    time.sleep(10)
            except Exception as e:
                print(f"Error in worker thread: {e}")

    @staticmethod
    def getSentLevels() -> [list[dict], list[dict]]:
        data = {
            "type": 27, # new sent levels type i think
            "secret": "Wmfd2893gb7"
        }

        headers = {
            "User-Agent": ""
        }

        req = requests.post('http://www.boomlings.com/database/getGJLevels21.php', data=data, headers=headers)

        if req.text == "-1": return []

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
