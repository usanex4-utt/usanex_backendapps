# backend/websocket/chat_manager.py
import logging
from fastapi import WebSocket

from backend.services.redis import presence_service
from .redis_manager import redis_ws_manager

logger = logging.getLogger(__name__)


class ChatConnectionManager:
    def __init__(self):
        # Multiple tabs / devices per user.
        self.active_connections: dict[int, set[WebSocket]] = {}
        redis_ws_manager.register("chat", self._handle_redis_message)

    async def _handle_redis_message(self, data: dict):
        receiver_id = data.get("receiver_id")
        if receiver_id is None:
            return
        sockets = list(self.active_connections.get(receiver_id, set()))
        if not sockets:
            return
        payload = data.get("payload")
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.error("Chat WS send error for user %s: %s", receiver_id, e)
                self._remove(receiver_id, ws)

    def _remove(self, user_id: int, ws: WebSocket):
        sockets = self.active_connections.get(user_id)
        if not sockets:
            return
        sockets.discard(ws)
        if not sockets:
            self.active_connections.pop(user_id, None)

    # ---------------- Lifecycle ----------------
    async def connect(self, user_id: int, websocket: WebSocket) -> bool:
        """Returns True if this is the user's FIRST socket (just came online)."""
        first = user_id not in self.active_connections
        self.active_connections.setdefault(user_id, set()).add(websocket)
        if first:
            await presence_service.set_online(user_id)
        return first

    async def disconnect(self, user_id: int, websocket: WebSocket) -> bool:
        """Returns True if this was the user's LAST socket (now offline)."""
        self._remove(user_id, websocket)
        if user_id not in self.active_connections:
            await presence_service.set_offline(user_id)
            return True
        return False

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active_connections

    # ---------------- Events ----------------
    async def send_presence(self, receiver_id, user_id, is_online, last_seen=None) -> bool:
        return await self.send_to_user(receiver_id, {
            "type": "presence",
            "user_id": user_id,
            "online": is_online,
            "last_seen": last_seen,
        })

    async def send_typing(self, sender_id, receiver_id, is_typing) -> bool:
        return await self.send_to_user(receiver_id, {
            "type": "typing",
            "user_id": sender_id,
            "typing": is_typing,
        })

    async def send_to_user(self, user_id: int, event: dict) -> bool:
        local = user_id in self.active_connections
        try:
            await redis_ws_manager.publish(
                {"receiver_id": user_id, "payload": event},
                scope="chat",
            )
        except Exception as e:
            logger.error("Chat Redis publish failed: %s", e)
            for ws in list(self.active_connections.get(user_id, set())):
                try:
                    await ws.send_json(event)
                except Exception:
                    pass
        return local


chat_manager = ChatConnectionManager()