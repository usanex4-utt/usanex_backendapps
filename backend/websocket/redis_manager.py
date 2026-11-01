# backend/websocket/redis_manager.py
import json
import asyncio
import logging
from typing import Callable, Awaitable, Optional
from backend.core.redis_client import get_redis

logger = logging.getLogger(__name__)


class RedisPubSubManager:
    def __init__(self):
        self.channel = "usanex:ws:broadcast"
        self.pubsub = None
        self._listen_task: Optional[asyncio.Task] = None
        self._callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._running = False

    def set_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """Set callback to handle incoming messages."""
        self._callback = callback

    async def publish(self, message: dict):
        """Broadcast a message to all connected workers."""
        r = get_redis()
        await r.publish(self.channel, json.dumps(message))

    async def start(self):
        """Start the listener. Called from lifespan AFTER init_redis()."""
        if self._running:
            return
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Redis WS listener started")

    async def _listen_loop(self):
        r = get_redis()
        self.pubsub = r.pubsub()
        await self.pubsub.subscribe(self.channel)
        logger.info(f"Subscribed to {self.channel}")

        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        if self._callback:
                            await self._callback(data)
                    except Exception as e:
                        logger.error(f"WS message handler error: {e}")
        except asyncio.CancelledError:
            logger.info("Redis WS listener cancelled")
        except Exception as e:
            logger.error(f"Redis WS listener error: {e}")
        finally:
            if self.pubsub:
                await self.pubsub.unsubscribe(self.channel)

    async def stop(self):
        """Stop the listener."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None


redis_ws_manager = RedisPubSubManager()