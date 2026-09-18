"""Bounded per-run fan-out: subscribers share one database poller per API replica."""

import asyncio
import contextlib
import time
from collections import deque
from .contracts import TERMINAL


class EventFeeds:
    def __init__(self, repository, notifier=None):
        self.repo, self.notifier = repository, notifier
        self.feeds = {}

    @contextlib.asynccontextmanager
    async def subscribe(self, user_id, run_id):
        key = (user_id, run_id)
        feed = self.feeds.get(key)
        if feed is None:
            feed = _Feed(self.repo, self.notifier, user_id, run_id)
            self.feeds[key] = feed
        feed.readers += 1
        try:
            yield feed
        finally:
            feed.readers -= 1
            if feed.readers == 0:
                self.feeds.pop(key, None)
                feed.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feed.task


class _Feed:
    def __init__(self, repo, notifier, user_id, run_id):
        self.repo, self.notifier, self.user_id, self.run_id = (
            repo,
            notifier,
            user_id,
            run_id,
        )
        self.buffer = deque(maxlen=256)
        self.readers = 0
        self.status = None
        self.error = None
        self.changed = asyncio.Condition()
        self.task = asyncio.create_task(self.poll())

    async def poll(self):
        position = 0
        last_status = 0
        try:
            while self.status is None:
                batch = await self.repo.events(self.user_id, self.run_id, position, 100)
                async with self.changed:
                    for event in batch:
                        position = event["seq"]
                        self.buffer.append(event)
                        if event["payload"].get("type") == "terminal":
                            self.status = event["payload"]["status"]
                    self.changed.notify_all()
                if batch:
                    continue
                if time.monotonic() - last_status > 1:
                    run = await self.repo.get_run(self.user_id, self.run_id)
                    last_status = time.monotonic()
                    if run["status"] in TERMINAL:
                        # Drain committed final events before signalling end.
                        final = await self.repo.events(
                            self.user_id, self.run_id, position, 100
                        )
                        if final:
                            async with self.changed:
                                self.buffer.extend(final)
                                position = final[-1]["seq"]
                                self.changed.notify_all()
                            continue
                        async with self.changed:
                            self.status = run["status"]
                            self.changed.notify_all()
                        break
                if self.notifier:
                    await self.notifier.wait(self.run_id, 0.1)
                else:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.changed:
                self.error = exc
                self.changed.notify_all()

    async def after(self, cursor):
        if self.error:
            raise self.error
        if self.buffer and cursor < self.buffer[0]["seq"] - 1:
            return await self.repo.events(self.user_id, self.run_id, cursor, 100)
        return [event for event in self.buffer if event["seq"] > cursor]
