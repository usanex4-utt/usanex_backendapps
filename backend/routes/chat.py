# ========================================================
# UTANEXA — CHAT ROUTES
# File: backend/routes/chat.py
# ========================================================

from datetime import datetime, timezone
from pathlib import Path
import uuid
import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database.database import SessionLocal
from ..database.models import User, UserSession, UserConnection, ChatMessage
from ..services.redis import session_service, presence_service, message_cache, idempotency
from ..websocket.chat_manager import chat_manager


router = APIRouter(prefix="/api/chat", tags=["Chat"])
SESSION_COOKIE_NAME = "usanex_session"

MAX_SIZES = {
    "image": 25 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
    "audio": 25 * 1024 * 1024,
    "document": 25 * 1024 * 1024,
}

ALLOWED_EXTENSIONS = {
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".webp": "image", ".gif": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio",
    ".m4a": "audio", ".aac": "audio", ".opus": "audio",
    ".pdf": "document", ".txt": "document", ".csv": "document",
    ".docx": "document", ".xlsx": "document", ".pptx": "document",
}


# =========================================================
# DATABASE / TIME
# =========================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# AUTHENTICATION (Redis-first)
# =========================================================

async def get_current_user(request: Request, db: Session):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            session_token = authorization[7:].strip()
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    # 1) Redis cache
    cached = await session_service.get_session(session_token)
    if cached:
        user = db.query(User).filter(User.id == cached.get("user_id")).first()
        if user is None:
            await session_service.delete_session(session_token, cached.get("user_id"))
            raise HTTPException(status_code=401, detail="User account not found.")
        if not getattr(user, "is_active", True):
            raise HTTPException(status_code=403, detail="This account is inactive.")
        return user

    # 2) DB fallback
    session = (
        db.query(UserSession)
        .filter(UserSession.session_token == session_token)
        .first()
    )
    if session is None or not session.is_active:
        raise HTTPException(status_code=401, detail="Invalid or inactive session.")

    if session.expires_at is None:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid session expiry.")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) >= expires_at:
        session.is_active = False
        db.commit()
        await session_service.delete_session(session_token, session.user_id)
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="User account not found.")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="This account is inactive.")

    # Refresh cache
    await session_service.cache_session(session_token, user.id, expires_at)

    session.last_used_at = utc_now()
    db.commit()
    return user


# =========================================================
# USER / CONNECTION HELPERS
# =========================================================

def find_user(db: Session, identifier: str):
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = db.query(User).filter(User.user_id == identifier).first()
    if user:
        return user
    if identifier.isdigit():
        return db.query(User).filter(User.id == int(identifier)).first()
    return None


def require_connected(db: Session, user_a_id: int, user_b_id: int):
    if user_a_id == user_b_id:
        raise HTTPException(status_code=400, detail="You cannot chat with yourself.")

    connection = (
        db.query(UserConnection)
        .filter(
            UserConnection.status.in_(("connected", "accepted")),
            or_(
                and_(
                    UserConnection.user_one_id == user_a_id,
                    UserConnection.user_two_id == user_b_id,
                ),
                and_(
                    UserConnection.user_one_id == user_b_id,
                    UserConnection.user_two_id == user_a_id,
                ),
            ),
        )
        .first()
    )
    if connection is None:
        raise HTTPException(
            status_code=403,
            detail="You can chat only with an accepted connection.",
        )
    return connection


def serialize_user(user):
    return {
        "id": user.id,
        "user_id": user.user_id,
        "username": user.username,
        "name": user.name,
        "profile_photo": user.profile_photo,
    }


def serialize_message(message):
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "receiver_id": message.receiver_id,
        "content": message.content,
        "message": message.content,
        "media_url": message.media_url,
        "media_type": message.media_type,
        "read": bool(message.is_read),
        "is_read": bool(message.is_read),
        "is_deleted": bool(message.is_deleted),
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


# =========================================================
# UPLOAD VALIDATION
# =========================================================

def detect_media_type(extension: str, data: bytes):
    if not data:
        return None

    if extension in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image"
    if extension == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if extension == ".gif" and data.startswith((b"GIF87a", b"GIF89a")):
        return "image"
    if extension == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"

    if extension in {".mp4", ".mov"} and len(data) >= 12 and data[4:8] == b"ftyp":
        return "video"
    if extension == ".webm" and data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"

    if extension == ".wav" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio"
    if extension in {".ogg", ".opus"} and data.startswith(b"OggS"):
        return "audio"
    if extension == ".mp3" and (
        data.startswith(b"ID3")
        or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
    ):
        return "audio"
    if extension == ".m4a" and len(data) >= 8 and data[4:8] == b"ftyp":
        return "audio"
    if extension == ".aac" and len(data) > 1 and data[0] == 0xFF and (data[1] & 0xF6) == 0xF0:
        return "audio"

    if extension == ".pdf" and data.startswith(b"%PDF-"):
        return "document"
    if extension in {".txt", ".csv"} and b"\x00" not in data[:4096]:
        return "document"
    if extension in {".docx", ".xlsx", ".pptx"} and data.startswith(b"PK\x03\x04"):
        return "document"

    return None


async def save_upload(file: UploadFile):
    original_name = (file.filename or "").replace("\\", "/").split("/")[-1]
    extension = Path(original_name).suffix.lower()
    expected_type = ALLOWED_EXTENSIONS.get(extension)

    if expected_type is None:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: images, videos, audio, PDF, TXT, CSV, DOCX, XLSX and PPTX.",
        )

    max_size = MAX_SIZES[expected_type]
    file_bytes = await file.read(max_size + 1)

    if len(file_bytes) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large. Maximum {expected_type} size is {max_size // (1024 * 1024)} MB.",
        )

    if detect_media_type(extension, file_bytes) != expected_type:
        raise HTTPException(
            status_code=400,
            detail="File content does not match the allowed file type.",
        )

    upload_dir = (
         Path(__file__).resolve().parents[1]
        / "static" / "uploads" / "chat"
    )
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    file_path = upload_dir / filename

    try:
        with file_path.open("xb") as output:
            output.write(file_bytes)
    except OSError:
        raise HTTPException(status_code=500, detail="Could not save uploaded file.")

    return f"/static/uploads/chat/{filename}", expected_type


# =========================================================
# HEALTH
# =========================================================

@router.get("/health/status")
def chat_health():
    return {
        "success": True,
        "app": "UtaNexa",
        "service": "chat",
        "status": "online",
    }


# =========================================================
# GET CHAT HISTORY (cache-first)
# =========================================================

@router.get("/{user_id}")
async def get_chat(
    user_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    current_user = await get_current_user(request, db)
    chat_user = find_user(db, user_id)

    if chat_user is None or not chat_user.is_active:
        raise HTTPException(status_code=404, detail="Chat user not found.")

    require_connected(db, current_user.id, chat_user.id)

    # Cache path (only recent page)
    if offset == 0 and limit <= 50:
        cached = await message_cache.get_recent_chat(current_user.id, chat_user.id, limit)
        if cached is not None:
            return {
                "success": True,
                "current_user": serialize_user(current_user),
                "user": serialize_user(chat_user),
                "messages": list(reversed(cached[:limit])),
                "cached": True,
            }

    messages = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.is_deleted == 0,
            or_(
                and_(
                    ChatMessage.sender_id == current_user.id,
                    ChatMessage.receiver_id == chat_user.id,
                ),
                and_(
                    ChatMessage.sender_id == chat_user.id,
                    ChatMessage.receiver_id == current_user.id,
                ),
            ),
        )
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    unread = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.sender_id == chat_user.id,
            ChatMessage.receiver_id == current_user.id,
            ChatMessage.is_read == 0,
            ChatMessage.is_deleted == 0,
        )
        .all()
    )
    for message in unread:
        message.is_read = 1
    if unread:
        db.commit()

    return {
        "success": True,
        "current_user": serialize_user(current_user),
        "user": serialize_user(chat_user),
        "messages": [serialize_message(message) for message in messages],
        "cached": False,
    }


# =========================================================
# SEND MESSAGE / MEDIA (idempotent + cache write-through)
# =========================================================

@router.post("/send")
async def send_message(
    request: Request,
    receiver_id: str = Form(...),
    content: str = Form(default=""),
    client_message_id: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    current_user = await get_current_user(request, db)
    receiver = find_user(db, receiver_id)

    if receiver is None or not receiver.is_active:
        raise HTTPException(status_code=404, detail="Receiver not found.")

    require_connected(db, current_user.id, receiver.id)

    clean_content = (content or "").strip()
    if len(clean_content) > 10000:
        raise HTTPException(status_code=400, detail="Message is too long (maximum 10,000 characters).")

    # Idempotency guard
    idem_key = None
    if client_message_id:
        idem_key = f"chat:{current_user.id}:{client_message_id}"
        if not await idempotency.acquire(idem_key):
            cached = await idempotency.get_result(idem_key)
            if cached:
                return cached
            raise HTTPException(status_code=409, detail="Message is being processed")

    media_url = None
    media_type = None
    if file is not None and file.filename:
        media_url, media_type = await save_upload(file)

    if not clean_content and not media_url:
        if idem_key:
            await idempotency.release(idem_key)
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    message = ChatMessage(
        sender_id=current_user.id,
        receiver_id=receiver.id,
        content=clean_content or None,
        media_url=media_url,
        media_type=media_type,
        is_read=0,
        is_deleted=0,
        created_at=utc_now(),
    )

    try:
        db.add(message)
        db.commit()
        db.refresh(message)
    except Exception:
        db.rollback()
        if idem_key:
            await idempotency.release(idem_key)
        if media_url:
            try:
                project_root = Path(__file__).resolve().parents[2]
                local_path = project_root / "frontend" / media_url.lstrip("/")
                if local_path.is_file():
                    local_path.unlink()
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="Message could not be saved.")

    # Write-through cache
    await message_cache.push_chat_message(message)
    await message_cache.incr_unread(receiver.id, room_id=0)

    serialized = serialize_message(message)
    response_body = {"success": True, "message": serialized}
    if idem_key:
        await idempotency.store_result(idem_key, response_body)

    # Realtime broadcast (cross-worker via Redis)
    await chat_manager.send_to_user(receiver.id, {"type": "message", "message": serialized})

    return response_body


# =========================================================
# CHAT PRESENCE / TYPING WEBSOCKET (Redis-backed)
# =========================================================

@router.websocket("/ws/{partner_user_id}")
async def chat_presence_websocket(
    websocket: WebSocket,
    partner_user_id: str,
):
    db = SessionLocal()

    current_user = None
    partner = None
    registered = False

    try:
        # ---------- AUTH ----------
        session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            session_token = websocket.query_params.get("token")

        if not session_token:
            logging.warning("Chat WS auth failed: no session cookie or token; partner=%s", partner_user_id)
            await websocket.close(code=4401)
            return

        # Redis cache first
        current_user = None
        cached = await session_service.get_session(session_token)
        if cached:
            current_user = (
                db.query(User)
                .filter(User.id == cached.get("user_id"))
                .first()
            )
            if current_user is not None and not getattr(current_user, "is_active", True):
                current_user = None

        # DB fallback
        if current_user is None:
            session = (
                db.query(UserSession)
                .filter(UserSession.session_token == session_token)
                .first()
            )
            if session is None or not session.is_active:
                logging.warning("Chat WS auth failed: session missing or inactive; partner=%s", partner_user_id)
                await websocket.close(code=4401)
                return
            if session.expires_at is None:
                await websocket.close(code=4401)
                return
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expires_at:
                await websocket.close(code=4401)
                return
            current_user = (
                db.query(User)
                .filter(User.id == session.user_id)
                .first()
            )
            if current_user is None or not getattr(current_user, "is_active", True):
                await websocket.close(code=4401)
                return
            await session_service.cache_session(session_token, current_user.id, expires_at)

        partner = find_user(db, partner_user_id)
        if partner is None or not partner.is_active:
            logging.warning("Chat WS rejected: partner missing or inactive; partner=%s", partner_user_id)
            await websocket.close(code=4404)
            return

        try:
            require_connected(db, current_user.id, partner.id)
        except HTTPException:
            logging.warning("Chat WS rejected: users are not connected; partner=%s", partner_user_id)
            await websocket.close(code=4403)
            return

        partner_id = partner.id
        partner_last_seen = _last_seen_value(partner)

        # ---------- ACCEPT + REGISTER ----------
        await websocket.accept()

        await chat_manager.connect(current_user.id, websocket)
        registered = True

        partner_online = await presence_service.is_online(partner_id)

        await websocket.send_json({
            "type": "presence",
            "user_id": partner_id,
            "online": partner_online,
            "last_seen": None if partner_online else partner_last_seen,
        })

        await chat_manager.send_presence(
            receiver_id=partner_id,
            user_id=current_user.id,
            is_online=True,
            last_seen=None,
        )

        # ---------- EVENT LOOP ----------
        while True:
            payload = await websocket.receive_json()

            if not isinstance(payload, dict):
                continue

            if payload.get("type") == "typing":
                await chat_manager.send_typing(
                    sender_id=current_user.id,
                    receiver_id=partner_id,
                    is_typing=payload.get("typing") is True,
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        logging.exception("Chat WS error")
    finally:
        if registered and current_user is not None:
            went_offline = await chat_manager.disconnect(current_user.id, websocket)

            if went_offline:
                last_seen = None
                try:
                    last_seen_db = SessionLocal()
                    try:
                        user = (
                            last_seen_db.query(User)
                            .filter(User.id == current_user.id)
                            .first()
                        )
                        if user is not None:
                            user.last_seen_at = utc_now()
                            last_seen_db.commit()
                            last_seen = _last_seen_value(user)
                    finally:
                        last_seen_db.close()
                except Exception:
                    pass

                if partner is not None:
                    await chat_manager.send_presence(
                        receiver_id=partner.id,
                        user_id=current_user.id,
                        is_online=False,
                        last_seen=last_seen,
                    )

        db.close()


def _last_seen_value(user):
    value = getattr(user, "last_seen_at", None)
    return value.isoformat() if value else None


# =========================================================
# MARK CHAT READ
# =========================================================

@router.post("/{user_id}/read")
async def mark_chat_read(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = await get_current_user(request, db)
    chat_user = find_user(db, user_id)

    if chat_user is None or not chat_user.is_active:
        raise HTTPException(status_code=404, detail="Chat user not found.")

    require_connected(db, current_user.id, chat_user.id)

    messages = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.sender_id == chat_user.id,
            ChatMessage.receiver_id == current_user.id,
            ChatMessage.is_read == 0,
            ChatMessage.is_deleted == 0,
        )
        .all()
    )
    for message in messages:
        message.is_read = 1

    db.commit()
    return {"success": True, "marked_read": len(messages)}


# =========================================================
# DELETE OWN MESSAGE (cache invalidated)
# =========================================================

@router.post("/message/{message_id}/delete")
async def delete_message(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = await get_current_user(request, db)
    message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can delete only your own message.")

    message.is_deleted = 1
    db.commit()

    await message_cache.invalidate_chat(message.sender_id, message.receiver_id)

    return {"success": True, "message": "Message deleted."}