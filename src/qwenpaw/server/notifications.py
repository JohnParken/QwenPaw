"""Best-effort Redis notifications; database polling always remains authoritative."""

import asyncio
import contextlib


class Notifications:
    def __init__(self, url=None):
        self.client = None
        self.task = None
        self.publisher = None
        self.pending = asyncio.Queue(maxsize=1024)
        self.waiters = {}
        if url:
            from redis.asyncio import Redis

            self.client = Redis.from_url(
                url, socket_connect_timeout=1, socket_timeout=1
            )

    async def start(self):
        if self.client:
            self.task = asyncio.create_task(self._listen())
            self.publisher = asyncio.create_task(self._publish())

    async def _listen(self):
        while True:
            try:
                async with self.client.pubsub() as channel:
                    await channel.subscribe("qwenpaw:server:events")
                    async for message in channel.listen():
                        if message["type"] == "message":
                            key = message["data"].decode()
                            for waiter in tuple(self.waiters.get(key, ())):
                                waiter.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1)

    async def publish(self, run_id):
        if self.client:
            with contextlib.suppress(asyncio.QueueFull):
                self.pending.put_nowait(run_id)

    async def _publish(self):
        while True:
            run_id = await self.pending.get()
            with contextlib.suppress(Exception):
                await self.client.publish("qwenpaw:server:events", run_id)

    async def wait(self, run_id, timeout):
        waiter = asyncio.Event()
        self.waiters.setdefault(run_id, set()).add(waiter)
        try:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(waiter.wait(), timeout)
        finally:
            self.waiters[run_id].discard(waiter)
            if not self.waiters[run_id]:
                self.waiters.pop(run_id)

    async def close(self):
        if self.publisher:
            self.publisher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.publisher
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.client:
            await self.client.aclose()
