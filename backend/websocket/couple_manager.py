# backend/websocket/couple_manager.py
import logging
from fastapi import WebSocket
from backend.services.redis import presence_service
from .redis_manager import redis_ws_manager

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, WebSocket] = {}
        # Register callback (but don't start listener yet)
        redis_ws_manager.set_callback(self._handle_redis_message)

    async def _handle_redis_message(self, data: dict):
        receiver_id = data.get("receiver_id")
        if receiver_id in self.active_connections:
            ws = self.active_connections[receiver_id]
            try:
                await ws.send_json(data.get("payload"))
            except Exception as e:
                logger.error(f"WS send error: {e}")

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        await presence_service.set_online(user_id)

    async def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        await presence_service.set_offline(user_id)

    async def send_personal_message(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_json(message)
        await redis_ws_manager.publish({
            "receiver_id": user_id,
            "payload": message
        })


couple_manager = ConnectionManager()