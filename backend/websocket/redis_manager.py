
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

    def register(
        self,
        scope: str,
        callback: Callable[[dict], Awaitable[None]]
    ):
        """Register callback for a scope (couple, chat, etc.)."""
        self._callbacks[scope] = callback

    def set_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """Deprecated: use register(scope, callback)."""
        self.register("broadcast", callback)

    # -----------------------------------------------------
    # Publish
    # -----------------------------------------------------

    async def publish(
        self,
        message: dict,
        scope: str = "broadcast"
    ):
        """Publish message to all connected workers."""

        r = get_redis()

        payload = dict(message)
        payload["scope"] = scope

        await r.publish(
            self.channel,
            json.dumps(payload)
        )

    # -----------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------

    async def start(self):
        """Start Redis listener with automatic reconnection."""

        if self._running:
            return

        self._running = True

        self._listen_task = asyncio.create_task(
            self._listen_forever()
        )

        logger.info("Redis WS listener started")

    async def _listen_forever(self):
        """Keep listener alive and reconnect when Redis fails."""

        backoff = 1

        while self._running:

            try:
                await self._listen_loop()

                backoff = 1

            except asyncio.CancelledError:
                raise

            except Exception as e:

                logger.error(
                    "Redis WS listener died: %s — retrying in %ss",
                    e,
                    backoff
                )

                await asyncio.sleep(backoff)

                backoff = min(backoff * 2, 30)

    # -----------------------------------------------------
    # Redis Listen Loop
    # -----------------------------------------------------

    async def _listen_loop(self):
        """Subscribe and listen for Redis messages."""

        r = get_redis()

        self.pubsub = r.pubsub()

        try:

            await self.pubsub.subscribe(self.channel)

            logger.info(
                "Subscribed to Redis channel: %s",
                self.channel
            )

            async for message in self.pubsub.listen():

                if not self._running:
                    break

                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])

                except (json.JSONDecodeError, TypeError):
                    logger.warning("Invalid Redis WS message")
                    continue

                scope = data.get("scope", "broadcast")

                callback = self._callbacks.get(scope)

                if callback is None:
                    continue

                try:
                    await callback(data)

                except Exception:
                    logger.exception(
                        "WS %s callback error",
                        scope
                    )

        finally:

            if self.pubsub:

                try:
                    await self.pubsub.unsubscribe(self.channel)
                except Exception:
                    pass

                try:
                    await self.pubsub.aclose()
                except Exception:
                    pass

                self.pubsub = None

    # -----------------------------------------------------
    # Stop
    # -----------------------------------------------------

    async def stop(self):
        """Stop Redis listener safely."""

        self._running = False

        if self._listen_task:

            self._listen_task.cancel()

            try:
                await self._listen_task

            except asyncio.CancelledError:
                pass

            self._listen_task = None

        logger.info("Redis WS listener stopped")


# ---------------------------------------------------------
# Global Instance
# ---------------------------------------------------------

redis_ws_manager = RedisPubSubManager()
