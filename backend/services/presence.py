# backend/services/presence.py

import asyncio
from collections import defaultdict
from backend.services.redis import presence_service

_lock = asyncio.Lock()
_counts: dict[int, int] = defaultdict(int)

async def add_connection(user_id: int) -> bool:
    """Returns True if user just came online."""
    async with _lock:
        was_offline = _counts[user_id] == 0
        _counts[user_id] += 1
        
    if was_offline:
        await presence_service.set_online(user_id)
        
    return was_offline

async def remove_connection(user_id: int) -> bool:
    """Returns True if user just went offline."""
    async with _lock:
        if _counts.get(user_id, 0) <= 0:
            return False
            
        _counts[user_id] -= 1
        went_offline = _counts[user_id] == 0
        
        if went_offline:
            del _counts[user_id]
            
    if went_offline:
        await presence_service.set_offline(user_id)
        
    return went_offline