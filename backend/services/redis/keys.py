# backend/services/redis/keys.py
PREFIX = "usanex"

def session(token: str) -> str:
    return f"{PREFIX}:session:{token}"

def user_sessions(user_id: int) -> str:
    return f"{PREFIX}:user_sessions:{user_id}"

def presence(user_id: int) -> str:
    return f"{PREFIX}:presence:{user_id}"

def room_pair(u1: int, u2: int) -> str:
    a, b = sorted((u1, u2))
    return f"{PREFIX}:room:pair:{a}:{b}"

def recent_messages(room_id: int) -> str:
    return f"{PREFIX}:msg:recent:{room_id}"

def unread(user_id: int) -> str:
    return f"{PREFIX}:unread:{user_id}"

def typing(room_id: int, user_id: int) -> str:
    return f"{PREFIX}:typing:{room_id}:{user_id}"

def otp(purpose: str, identifier: str) -> str:
    return f"{PREFIX}:otp:{purpose}:{identifier}"

def otp_attempts(purpose: str, identifier: str) -> str:
    return f"{PREFIX}:otp:attempts:{purpose}:{identifier}"

def rate_limit(action: str, identifier: str) -> str:
    return f"{PREFIX}:rl:{action}:{identifier}"

def idempotency(key: str) -> str:
    return f"{PREFIX}:idem:{key}"