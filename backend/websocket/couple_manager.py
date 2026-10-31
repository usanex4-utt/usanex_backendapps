# =========================================================
# USANEX — COUPLE WEBSOCKET CONNECTION MANAGER
# =========================================================

import time
from typing import Optional

from fastapi import WebSocket


class CoupleConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._heartbeats: dict[WebSocket, float] = {}

    # --------------------------------------------------
    # Lifecycle
    # --------------------------------------------------

    async def connect(
        self,
        user_id: int,
        websocket: WebSocket,
    ) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)
        self._heartbeats[websocket] = time.time()

    async def disconnect(
        self,
        user_id: int,
        websocket: WebSocket,
    ) -> None:
        sockets = self._connections.get(user_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)
        self._heartbeats.pop(websocket, None)

    # --------------------------------------------------
    # Online check
    # --------------------------------------------------

    def is_online(self, user_id: int) -> bool:
        return bool(self._connections.get(user_id))

    # --------------------------------------------------
    # Delivery
    # --------------------------------------------------

    async def send_to_user(
        self,
        user_id: int,
        event: dict,
    ) -> bool:
        sockets = list(self._connections.get(user_id, set()))
        delivered = False

        for ws in sockets:
            try:
                await ws.send_json(event)
                delivered = True
            except Exception:
                self._connections.get(user_id, set()).discard(ws)
                self._heartbeats.pop(ws, None)

        return delivered

    # --------------------------------------------------
    # Typed events
    # --------------------------------------------------

    async def send_presence(
        self,
        receiver_id: int,
        user_id: int,
        is_online: bool,
        last_seen: Optional[str],
    ) -> None:
        await self.send_to_user(
            receiver_id,
            {
                "type": "presence",
                "user_id": user_id,
                "is_online": is_online,
                "last_seen": last_seen,
            },
        )

    async def send_typing(
        self,
        sender_id: int,
        receiver_id: int,
        is_typing: bool,
    ) -> None:
        await self.send_to_user(
            receiver_id,
            {
                "type": "typing",
                "user_id": sender_id,
                "typing": is_typing,
            },
        )

    # --------------------------------------------------
    # Heartbeat
    # --------------------------------------------------

    async def touch(self, websocket: WebSocket) -> None:
        self._heartbeats[websocket] = time.time()

    def heartbeat_age(
        self,
        websocket: WebSocket,
    ) -> Optional[float]:
        last = self._heartbeats.get(websocket)
        if last is None:
            return None
        return time.time() - last


couple_manager = CoupleConnectionManager()