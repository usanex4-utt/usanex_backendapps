# ========================================================
# UTANEXA — CHAT ROUTES
# backend/routes/chat.py
#
# Existing API paths are preserved.
# Only accepted connections may access/send normal chat messages.
# Supports text/links plus validated image, video, audio and document uploads.
# =========================================================

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
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database.database import SessionLocal
from ..database.models import User, UserSession, UserConnection, ChatMessage

router = APIRouter(prefix="/api/chat", tags=["Chat"])
SESSION_COOKIE_NAME = "usanex_session"

MAX_SIZES = {
    "image": 25 * 1024 * 1024,       # 25 MB
    "video": 100 * 1024 * 1024,      # 100 MB
    "audio": 25 * 1024 * 1024,       # 25 MB
    "document": 25 * 1024 * 1024,    # 25 MB
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
    # Existing database columns store naive UTC timestamps.
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# AUTHENTICATION
# =========================================================

def get_current_user(request: Request, db: Session):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            session_token = authorization[7:].strip()
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")

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
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="User account not found.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is inactive.")

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

    # Legacy support for numeric database IDs.
    if identifier.isdigit():
        return db.query(User).filter(User.id == int(identifier)).first()
    return None


def require_connected(db: Session, user_a_id: int, user_b_id: int):
    """Require an explicit accepted connection in either stored direction."""
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
    """Validate common signatures rather than trusting filename/MIME alone."""
    if not data:
        return None

    # Images
    if extension in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image"
    if extension == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if extension == ".gif" and data.startswith((b"GIF87a", b"GIF89a")):
        return "image"
    if extension == ".webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"

    # Video
    if extension in {".mp4", ".mov"} and len(data) >= 12 and data[4:8] == b"ftyp":
        return "video"
    if extension == ".webm" and data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"

    # Audio
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

    # Documents
    if extension == ".pdf" and data.startswith(b"%PDF-"):
        return "document"
    if extension in {".txt", ".csv"} and b"\x00" not in data[:4096]:
        return "document"
    if extension in {".docx", ".xlsx", ".pptx"} and data.startswith(b"PK\x03\x04"):
        return "document"

    return None


async def save_upload(file: UploadFile):
    # Remove directory components from a user-supplied filename.
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

    # Note: Render's local filesystem may be ephemeral. Persistent/object storage
    # (for example, Cloudinary or an object-storage bucket) should replace this
    # local destination before relying on uploads for production.
    upload_dir = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "static" / "uploads" / "chat"
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
# GET CHAT HISTORY
# =========================================================

@router.get("/{user_id}")
def get_chat(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    chat_user = find_user(db, user_id)

    if chat_user is None or not chat_user.is_active:
        raise HTTPException(status_code=404, detail="Chat user not found.")

    require_connected(db, current_user.id, chat_user.id)

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
        .all()
    )

    # Preserve current frontend behavior; loading this conversation marks
    # incoming messages as read. Later, frontend visibility can drive this call.
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
    }


# =========================================================
# SEND MESSAGE / MEDIA
# =========================================================

@router.post("/send")
async def send_message(
    request: Request,
    receiver_id: str = Form(...),
    content: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    receiver = find_user(db, receiver_id)

    if receiver is None or not receiver.is_active:
        raise HTTPException(status_code=404, detail="Receiver not found.")

    require_connected(db, current_user.id, receiver.id)

    clean_content = (content or "").strip()
    if len(clean_content) > 10000:
        raise HTTPException(status_code=400, detail="Message is too long (maximum 10,000 characters).")

    media_url = None
    media_type = None

    if file is not None and file.filename:
        media_url, media_type = await save_upload(file)

    # A URL can be sent as ordinary text; safe clickable rendering is frontend work.
    if not clean_content and not media_url:
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
        if media_url:
            try:
                # media_url begins with /static/...; resolve from the project
                # frontend directory, not frontend/frontend (the old path bug).
                relative_media_path = media_url.lstrip("/")
                project_root = Path(__file__).resolve().parents[2]
                local_path = project_root / "frontend" / relative_media_path
                if local_path.is_file():
                    local_path.unlink()
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="Message could not be saved.")

    return {"success": True, "message": serialize_message(message)}

# =========================================================
# CHAT PRESENCE / TYPING
# =========================================================

# Active chat sockets for this backend process.
_chat_sockets: dict[int, set[WebSocket]] = {}


async def _send_chat_event(websocket: WebSocket, payload: dict):
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False


async def _send_user_event(user_id: int, payload: dict):
    sockets = list(_chat_sockets.get(user_id, set()))

    for socket in sockets:
        sent = await _send_chat_event(socket, payload)

        if not sent:
            _chat_sockets.get(user_id, set()).discard(socket)


def _last_seen_value(user):
    value = getattr(user, "last_seen_at", None)
    return value.isoformat() if value else None


@router.websocket("/ws/{partner_user_id}")
async def chat_presence_websocket(
    websocket: WebSocket,
    partner_user_id: str,
):
    db = SessionLocal()

    current_id = None
    partner_id = None
    registered = False

    try:
        # Authenticate with the existing session cookie OR token query param.
        session_token = websocket.cookies.get(SESSION_COOKIE_NAME)

        if not session_token:
            session_token = websocket.query_params.get("token")

        if not session_token:
            logging.warning('Chat WS auth failed: no session cookie or token; partner=%s', partner_user_id)
            await websocket.close(code=4401)
            return

        session = (
            db.query(UserSession)
            .filter(UserSession.session_token == session_token)
            .first()
        )

        if session is None or not session.is_active:
            logging.warning('Chat WS auth failed: session missing or inactive; partner=%s', partner_user_id)
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

        if current_user is None or not current_user.is_active:
            await websocket.close(code=4401)
            return

        partner = find_user(db, partner_user_id)

        if partner is None or not partner.is_active:
            logging.warning("Chat WS rejected: partner missing or inactive; partner=%s", partner_user_id)
            await websocket.close(code=4404)
            return

        # Enforce the existing accepted-connection rule.
        try:
            require_connected(db, current_user.id, partner.id)
        except HTTPException:
            logging.warning("Chat WS rejected: users are not connected; partner=%s", partner_user_id)
            await websocket.close(code=4403)
            return

        current_id = current_user.id
        partner_id = partner.id
        partner_last_seen = _last_seen_value(partner)

        session.last_used_at = utc_now()
        db.commit()

        # Do not keep a database connection open during the socket lifetime.
        db.close()

        await websocket.accept()

        _chat_sockets.setdefault(current_id, set()).add(websocket)
        registered = True

        partner_online = bool(_chat_sockets.get(partner_id))

        # Send the partner's current status to this user.
        await _send_chat_event(
            websocket,
            {
                "type": "presence",
                "user_id": partner_id,
                "online": partner_online,
                "last_seen": (
                    None if partner_online
                    else partner_last_seen
                ),
            },
        )

        # Notify the partner that this user is online.
        await _send_user_event(
            partner_id,
            {
                "type": "presence",
                "user_id": current_id,
                "online": True,
                "last_seen": None,
            },
        )

        # Receive typing events and forward them to the partner.
        while True:
            payload = await websocket.receive_json()

            if not isinstance(payload, dict):
                continue

            if payload.get("type") == "typing":
                await _send_user_event(
                    partner_id,
                    {
                        "type": "typing",
                        "user_id": current_id,
                        "typing": payload.get("typing") is True,
                    },
                )

    except WebSocketDisconnect:
        pass

    except Exception:
        # Socket errors must not affect normal chat APIs.
        pass

    finally:
        if registered and current_id is not None:
            sockets = _chat_sockets.get(current_id, set())
            sockets.discard(websocket)

            # Update last seen only after the final socket disconnects.
            if not sockets:
                _chat_sockets.pop(current_id, None)
                last_seen = None

                try:
                    last_seen_db = SessionLocal()

                    try:
                        user = (
                            last_seen_db.query(User)
                            .filter(User.id == current_id)
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

                if partner_id is not None:
                    await _send_user_event(
                        partner_id,
                        {
                            "type": "presence",
                            "user_id": current_id,
                            "online": False,
                            "last_seen": last_seen,
                        },
                    )

        db.close()

    


# =========================================================
# MARK CHAT READ
# =========================================================

@router.post("/{user_id}/read")
def mark_chat_read(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
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
# DELETE OWN MESSAGE
# =========================================================

@router.post("/message/{message_id}/delete")
def delete_message(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    message = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can delete only your own message.")

    message.is_deleted = 1
    db.commit()
    return {"success": True, "message": "Message deleted."}
