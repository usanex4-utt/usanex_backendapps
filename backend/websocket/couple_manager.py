# backend/websocket/couple_manager.py
import logging
import time
from typing import Optional

from fastapi import WebSocket

from backend.services.redis import presence_service
from .redis_manager import redis_ws_manager

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # One active WebSocket per user (last tab wins).
        self.active_connections: dict[int, WebSocket] = {}
        self._last_activity: dict[WebSocket, float] = {}
        # Register scoped callback so chat messages do NOT leak here
        redis_ws_manager.register("couple", self._handle_redis_message)

    # -------------------------------------------------
    # Redis callback — cross-worker delivery
    # -------------------------------------------------
    async def _handle_redis_message(self, data: dict):
        receiver_id = data.get("receiver_id")
        if receiver_id is None:
            return

        ws = self.active_connections.get(receiver_id)
        if ws is None:
            return

        try:
            await ws.send_json(data.get("payload"))
        except Exception as e:
            logger.error("Couple WS send error for user %s: %s", receiver_id, e)
            self.active_connections.pop(receiver_id, None)
            self._last_activity.pop(ws, None)

    # -------------------------------------------------
    # Lifecycle
    # -------------------------------------------------
    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()

        old_ws = self.active_connections.get(user_id)
        if old_ws is not None and old_ws is not websocket:
            self._last_activity.pop(old_ws, None)

        self.active_connections[user_id] = websocket
        await self.touch(websocket)
        await presence_service.set_online(user_id)

    async def disconnect(self, user_id: int, websocket: WebSocket | None = None):
        current = self.active_connections.get(user_id)

        if current is not None and (websocket is None or current is websocket):
            self.active_connections.pop(user_id, None)
            self._last_activity.pop(current, None)
            await presence_service.set_offline(user_id)
        elif websocket is not None:
            self._last_activity.pop(websocket, None)

    # -------------------------------------------------
    # Heartbeat
    # -------------------------------------------------
    async def touch(self, websocket: WebSocket) -> None:
        self._last_activity[websocket] = time.monotonic()
        # Refresh Redis presence TTL for whoever owns this socket
        for uid, ws in self.active_connections.items():
            if ws is websocket:
                try:
                    await presence_service.set_online(uid)
                except Exception:
                    pass
                break

    def heartbeat_age(self, websocket: WebSocket) -> Optional[float]:
        last = self._last_activity.get(websocket)
        if last is None:
            return None
        return time.monotonic() - last

    # -------------------------------------------------
    # Presence
    # -------------------------------------------------
    def is_online(self, user_id: int) -> bool:
        return user_id in self.active_connections

    async def send_presence(
        self,
        receiver_id: int,
        user_id: int,
        is_online: bool,
        last_seen: Optional[str] = None,
    ) -> bool:
        return await self.send_to_user(receiver_id, {
            "type": "presence",
            "user_id": user_id,
            "online": is_online,
            "last_seen": last_seen,
        })

    async def send_typing(
        self,
        sender_id: int,
        receiver_id: int,
        is_typing: bool,
    ) -> bool:
        return await self.send_to_user(receiver_id, {
            "type": "typing",
            "user_id": sender_id,
            "typing": is_typing,
        })

    # -------------------------------------------------
    # Generic delivery via Redis pub/sub (scope="couple")
    # -------------------------------------------------
    async def send_to_user(self, user_id: int, event: dict) -> bool:
        """
        Publish to Redis. Every worker (including this one) receives it via
        _handle_redis_message and delivers locally if the user is connected.

        Returns True if the user has an active socket on THIS worker.
        Cross-worker 'delivered' is best-effort — the client will send
        message_delivered once it actually arrives.
        """
        local = user_id in self.active_connections

        try:
            await redis_ws_manager.publish(
                {"receiver_id": user_id, "payload": event},
                scope="couple",
            )
        except Exception as e:
            logger.error("Couple Redis publish failed for user %s: %s", user_id, e)
            # Best-effort local fallback
            ws = self.active_connections.get(user_id)
            if ws is not None:
                try:
                    await ws.send_json(event)
                except Exception:
                    pass

        return local

    # Backwards-compat alias (couple_chat.py legacy)
    async def send_personal_message(self, user_id: int, message: dict):
        return await self.send_to_user(user_id, message)


couple_manager = ConnectionManager()