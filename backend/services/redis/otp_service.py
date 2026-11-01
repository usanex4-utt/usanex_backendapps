# backend/services/redis/otp_service.py
import hashlib
import secrets
from backend.core.redis_client import get_redis
from . import keys

OTP_TTL = 600 # 10 mins
MAX_ATTEMPTS = 3

def _hash(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()

async def generate_otp(purpose: str, identifier: str) -> str:
    otp = f"{secrets.randbelow(1_000_000):06d}"
    r = get_redis()
    await r.set(keys.otp(purpose, identifier), _hash(otp), ex=OTP_TTL)
    await r.delete(keys.otp_attempts(purpose, identifier))
    return otp

async def verify_otp(purpose: str, identifier: str, otp: str) -> bool:
    r = get_redis()
    key = keys.otp(purpose, identifier)
    att_key = keys.otp_attempts(purpose, identifier)
    
    stored = await r.get(key)
    if not stored: return False
    
    attempts = await r.incr(att_key)
    if attempts == 1: await r.expire(att_key, OTP_TTL)
    if attempts > MAX_ATTEMPTS:
        await r.delete(key, att_key)
        return False
        
    if secrets.compare_digest(stored, _hash(otp)):
        await r.delete(key, att_key)
        return True
    return False