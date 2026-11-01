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
        self._callbacks: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._running = False

    # -----------------------------------------------------
    # Registration
    # -----------------------------------------------------
    def register(self, scope: str, callback: Callable[[dict], Awaitable[None]]):
        """Register a callback for a scope (e.g. 'couple', 'chat')."""
        self._callbacks[scope] = callback

    def set_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """Deprecated: use register(scope, callback). Defaults to 'broadcast'."""
        self.register("broadcast", callback)

    # -----------------------------------------------------
    # Publish
    # -----------------------------------------------------
    async def publish(self, message: dict, scope: str = "broadcast"):
        """Broadcast a message to all connected workers in a scope."""
        r = get_redis()
        payload = dict(message)
        payload["scope"] = scope
        await r.publish(self.channel, json.dumps(payload))

    # -----------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------
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
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                scope = data.get("scope", "broadcast")
                cb = self._callbacks.get(scope)
                if cb is None:
                    continue
                try:
                    await cb(data)
                except Exception as e:
                    logger.error(f"WS {scope} callback error: {e}")
        except asyncio.CancelledError:
            logger.info("Redis WS listener cancelled")
            raise
        except Exception as e:
            logger.error(f"Redis WS listener error: {e}")
        finally:
            if self.pubsub:
                try:
                    await self.pubsub.unsubscribe(self.channel)
                except Exception:
                    pass

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