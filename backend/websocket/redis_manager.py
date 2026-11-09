
# =========================================================
# USANEX - UPSTASH REDIS WEBSOCKET MANAGER
# backend/websocket/redis_manager.py
# =========================================================

import json
import asyncio
import logging

from typing import Callable, Awaitable, Optional

from redis.exceptions import RedisError

from backend.core.redis_client import get_redis

logger = logging.getLogger(__name__)


class RedisPubSubManager:

    def __init__(self):

        self.channel = "usanex:ws:broadcast"

        self.pubsub = None

        self._listen_task: Optional[asyncio.Task] = None

        self._callbacks: dict[
            str,
            Callable[[dict], Awaitable[None]]
        ] = {}

        self._running = False

    # =====================================================
    # REGISTER CALLBACK
    # =====================================================

    def register(
        self,
        scope: str,
        callback: Callable[[dict], Awaitable[None]]
    ):

        self._callbacks[scope] = callback

        logger.info(
            "Redis WS callback registered: %s",
            scope
        )

    # =====================================================
    # LEGACY CALLBACK SUPPORT
    # =====================================================

    def set_callback(
        self,
        callback: Callable[[dict], Awaitable[None]]
    ):

        self.register("broadcast", callback)

    # =====================================================
    # PUBLISH MESSAGE
    # =====================================================

    async def publish(
        self,
        message: dict,
        scope: str = "broadcast"
    ):

        r = get_redis()

        payload = dict(message)

        payload["scope"] = scope

        try:

            await r.publish(
                self.channel,
                json.dumps(payload, ensure_ascii=False)
            )

        except RedisError:

            logger.exception(
                "Redis publish failed for scope: %s",
                scope
            )

            raise

    # =====================================================
    # START REDIS LISTENER
    # =====================================================

    async def start(self):

        if self._running:
            return

        self._running = True

        self._listen_task = asyncio.create_task(
            self._listen_forever(),
            name="usanex-redis-listener"
        )

        logger.info(
            "Upstash Redis WS listener started"
        )

    # =====================================================
    # AUTOMATIC RECONNECTION
    # =====================================================

    async def _listen_forever(self):

        backoff = 1

        while self._running:

            try:

                await self._listen_loop()

                if self._running:

                    raise ConnectionError(
                        "Redis listener stopped unexpectedly"
                    )

                backoff = 1

            except asyncio.CancelledError:

                raise

            except Exception as e:

                logger.error(
                    "Redis listener error: %s. "
                    "Retrying in %s seconds.",
                    e,
                    backoff
                )

                if not self._running:
                    break

                await asyncio.sleep(backoff)

                backoff = min(backoff * 2, 30)

    # =====================================================
    # REDIS SUBSCRIBE LOOP
    # =====================================================

    async def _listen_loop(self):

        r = get_redis()

        self.pubsub = r.pubsub()

        try:

            await self.pubsub.subscribe(
                self.channel
            )

            logger.info(
                "Subscribed to Upstash channel: %s",
                self.channel
            )

            while self._running:

                message = await self.pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0
                )

                if message is None:
                    await asyncio.sleep(0.05)
                    continue

                if message.get("type") != "message":
                    continue

                # -----------------------------------------
                # DECODE MESSAGE
                # -----------------------------------------

                try:

                    data = json.loads(
                        message["data"]
                    )

                except (
                    json.JSONDecodeError,
                    TypeError
                ):

                    logger.warning(
                        "Invalid Redis message received"
                    )

                    continue

                # -----------------------------------------
                # GET CALLBACK
                # -----------------------------------------

                scope = data.get(
                    "scope",
                    "broadcast"
                )

                callback = self._callbacks.get(
                    scope
                )

                if callback is None:
                    continue

                # -----------------------------------------
                # EXECUTE CALLBACK
                # -----------------------------------------

                try:

                    await callback(data)

                except Exception:

                    logger.exception(
                        "WebSocket callback failed: %s",
                        scope
                    )

        finally:

            if self.pubsub is not None:

                try:

                    await self.pubsub.unsubscribe(
                        self.channel
                    )

                except Exception:
                    pass

                try:

                    await self.pubsub.aclose()

                except Exception:

                    logger.exception(
                        "Redis PubSub close failed"
                    )

                self.pubsub = None

    # =====================================================
    # STOP REDIS LISTENER
    # =====================================================

    async def stop(self):

        self._running = False

        if self._listen_task is not None:

            self._listen_task.cancel()

            try:

                await self._listen_task

            except asyncio.CancelledError:
                pass

            except Exception:

                logger.exception(
                    "Redis listener shutdown error"
                )

            self._listen_task = None

        logger.info(
            "Upstash Redis WS listener stopped"
        )


# =========================================================
# GLOBAL INSTANCE
# =========================================================

redis_ws_manager = RedisPubSubManager()
