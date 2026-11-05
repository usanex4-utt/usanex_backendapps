# backend/routes/moment.py
import json
import logging
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database.database import get_db
from ..database.moment_migrate import run_moment_migrations
from ..services.supabase_storage import (
    upload_moment_media, delete_moment_media, get_supabase_client,
)
from .auth import get_current_user_from_request


logger = logging.getLogger(__name__)


# ── LAZY SCHEMA GUARD ────────────────────────────────────
def ensure_moment_schema():
    try:
        run_moment_migrations()
    except Exception:
        logger.exception("[Usanex Moments] Schema check failed")
        raise HTTPException(500, "Moment schema unavailable")


router = APIRouter(
    prefix="/api/moments",
    tags=["Nex Moments"],
    dependencies=[Depends(ensure_moment_schema)],
)


# ── constants ────────────────────────────────────────────
VALID_TYPES = {"text", "image", "video", "gif", "countdown", "emoji_slider"}
VALID_AUDIENCE = {"connections", "following", "followers", "selected_people", "only_me"}
VALID_REACTIONS = {"❤️", "😂", "😮", "😢", "🔥", "👍", "🙏", "🎉"}

ALLOWED_MEDIA = {
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
    "image/gif": ("image", ".gif"),
    "video/mp4": ("video", ".mp4"),
    "video/webm": ("video", ".webm"),
}
MAX_MEDIA = 50 * 1024 * 1024


# ── helpers ──────────────────────────────────────────────
async def get_authenticated_user(request: Request, db: Session):
    user = await get_current_user_from_request(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _signed(path: Optional[str], expires: int = 3600) -> Optional[str]:
    """
    Return a browser-loadable URL for stored media.

    - "/static/..."  → local mode, already a URL, pass through unchanged
    - "http(s)://..." → already a URL, pass through
    - anything else   → Supabase storage key, mint a signed URL
    """
    if not path:
        return None
    # Local mode or already absolute → pass through
    if path.startswith(("http://", "https://", "/")):
        return path
    # Supabase mode → mint a signed URL
    try:
        sb = get_supabase_client()
        r = sb.storage.from_("nex-moments").create_signed_url(path, expires)
        return r.get("signedURL") or r.get("signed_url") or r.get("signedUrl")
    except Exception:
        logger.warning("signed url failed for %s", path, exc_info=True)
        return None


def _norm_ids(ids):
    return list(dict.fromkeys(x.strip() for x in (ids or []) if x and x.strip()))


def _validate_audience(audience, scope, selected):
    selected = _norm_ids(selected)
    if scope == "couple":
        if audience != "only_me" or selected:
            raise HTTPException(400, "Couple moment must use only_me")
        return selected
    if scope != "normal":
        raise HTTPException(400, "Invalid moment scope")
    if audience not in VALID_AUDIENCE:
        raise HTTPException(400, "Invalid audience")
    if audience == "selected_people" and not selected:
        raise HTTPException(400, "Select at least one person")
    if audience != "selected_people" and selected:
        raise HTTPException(400, "selected_user_ids only for selected_people")
    return selected


def _save_audience(db, moment_id, ids, creator):
    ids = _norm_ids(ids)
    if len(ids) > 500:
        raise HTTPException(400, "Maximum 500 selected users")

    if ids:
        eligible = db.execute(text("""
            SELECT DISTINCT s.user_id
              FROM public.users c
              JOIN public.users s ON s.user_id = ANY(:ids)
             WHERE c.user_id = :creator AND c.id != s.id
               AND (
                   EXISTS (SELECT 1 FROM public.user_connections conn
                            WHERE conn.status = 'connected'
                              AND ((conn.user_one_id = c.id AND conn.user_two_id = s.id)
                                OR (conn.user_two_id = c.id AND conn.user_one_id = s.id)))
                OR EXISTS (SELECT 1 FROM public.user_follows f
                            WHERE f.follower_id = c.id AND f.following_id = s.id)
                OR EXISTS (SELECT 1 FROM public.user_follows f
                            WHERE f.follower_id = s.id AND f.following_id = c.id)
               )
        """), {"ids": ids, "creator": creator}).scalars().all()

        if set(eligible) != set(ids):
            raise HTTPException(400, "Some users are not eligible")

    db.execute(text("DELETE FROM public.nex_moment_audience WHERE moment_id = :m"),
               {"m": moment_id})
    if ids:
        db.execute(text("""
            INSERT INTO public.nex_moment_audience (moment_id, allowed_user_id)
            VALUES (:m, :uid)
        """), [{"m": moment_id, "uid": u} for u in ids])


def _audit(db, moment_id, user_id, action, old=None, new=None):
    try:
        db.execute(text("""
            INSERT INTO nex_moment_audit_logs
                (moment_id, user_id, action, old_data, new_data, action_at)
            VALUES (:m, :u, :a,
                    CAST(:old AS jsonb), CAST(:new AS jsonb), NOW())
        """), {"m": moment_id, "u": user_id, "a": action,
               "old": json.dumps(old or {}), "new": json.dumps(new or {})})
    except Exception:
        logger.warning("audit log failed", exc_info=True)


def _notify(db, recipient, actor, moment_id, kind, msg):
    try:
        if actor == recipient:
            return
        pref = db.execute(text(
            "SELECT notifications_enabled FROM nex_moment_settings WHERE user_id = :u"
        ), {"u": recipient}).scalar()
        if pref is False:
            return
        db.execute(text("""
            INSERT INTO nex_moment_notifications
                (recipient_user_id, actor_user_id, moment_id, notification_type, message)
            VALUES (:r, :a, :m, :k, :msg)
        """), {"r": recipient, "a": actor, "m": moment_id, "k": kind, "msg": msg})
    except Exception:
        logger.warning("notify failed", exc_info=True)


def _get_moment_row(db, moment_id):
    return db.execute(text("""
        SELECT id, user_id, moment_type, caption, media_url, audience,
               moment_scope, moderation_status, metadata,
               created_at, expires_at, archived_at, deleted_at
          FROM nex_moments WHERE id = :id
    """), {"id": moment_id}).mappings().first()


def _can_view(db, moment_id, viewer, scope):
    fn = "can_view_couple_moment" if scope == "couple" else "can_view_normal_moment"
    return bool(db.execute(text(f"SELECT {fn}(:m, :v)"),
                           {"m": moment_id, "v": viewer}).scalar())


def _serialize_moment(db, row, viewer):
    mid = row["id"]
    counts = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM nex_moment_views    WHERE moment_id = :m) AS views,
          (SELECT COUNT(*) FROM nex_moment_reactions WHERE moment_id = :m) AS reactions,
          (SELECT COUNT(*) FROM nex_moment_comments  WHERE moment_id = :m AND is_deleted = FALSE) AS comments,
          (SELECT reaction FROM nex_moment_reactions WHERE moment_id = :m AND user_id = :v) AS my_reaction,
          (SELECT 1 FROM nex_moment_views WHERE moment_id = :m AND viewer_user_id = :v LIMIT 1) AS viewed
    """), {"m": mid, "v": viewer}).mappings().first()

    meta = row["metadata"] or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    return {
        "id": mid,
        "user_id": row["user_id"],
        "moment_type": row["moment_type"],
        "caption": row["caption"],
        "media_url": row["media_url"],
        "signed_media_url": _signed(row["media_url"]),
        "audience": row["audience"],
        "moment_scope": row["moment_scope"],
        "moderation_status": row["moderation_status"],
        "metadata": meta,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "is_mine": row["user_id"] == viewer,
        "viewed": bool(counts and counts["viewed"]),
        "views_count": int(counts["views"]) if counts else 0,
        "reactions_count": int(counts["reactions"]) if counts else 0,
        "comments_count": int(counts["comments"]) if counts else 0,
        "my_reaction": counts["my_reaction"] if counts else None,
    }


def _detect_mime(data):
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")): return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp": return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"): return "video/webm"
    return None


# ── pydantic ─────────────────────────────────────────────
class MomentCreateBody(BaseModel):
    moment_type: Literal["text", "countdown", "emoji_slider", "gif"] = "text"
    caption: str = Field(default="", max_length=2000)
    audience: Literal[
        "connections", "following", "followers", "selected_people", "only_me"
    ] = "connections"
    moment_scope: Literal["normal", "couple"] = "normal"
    selected_user_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    gif_url: Optional[str] = None

class MomentUpdateBody(BaseModel):
    caption: Optional[str] = Field(default=None, max_length=2000)
    audience: Optional[Literal[
        "connections", "following", "followers", "selected_people", "only_me"
    ]] = None
    selected_user_ids: Optional[list[str]] = None
    metadata: Optional[dict] = None

class ReactionBody(BaseModel):
    reaction: str = Field(min_length=1, max_length=30)

class CommentBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_comment_id: Optional[int] = None

class ReportBody(BaseModel):
    reason: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)

class SettingsBody(BaseModel):
    default_audience: Optional[Literal[
        "connections", "following", "followers", "only_me"
    ]] = None
    comments_enabled: Optional[bool] = None
    reactions_enabled: Optional[bool] = None
    viewers_visible: Optional[bool] = None
    notifications_enabled: Optional[bool] = None


# =========================================================
# ⚠️  STATIC ROUTES FIRST — MUST come before /{moment_id}
# =========================================================

@router.get("/health")
def health():
    return {"success": True, "service": "moments", "status": "online"}


@router.get("/_diag")
def diag(db: Session = Depends(get_db)):
    try:
        tables = db.execute(text("""
            SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name LIKE 'nex_moment%'
             ORDER BY table_name
        """)).scalars().all()

        fns = db.execute(text("""
            SELECT routine_name FROM information_schema.routines
             WHERE routine_schema = 'public'
               AND routine_name IN ('can_view_normal_moment','can_view_couple_moment')
        """)).scalars().all()

        return {
            "success": True,
            "tables": tables,
            "functions": fns,
            "ready": "nex_moments" in tables and len(fns) == 2,
        }
    except Exception as exc:
        logger.exception("diag failed")
        raise HTTPException(500, str(exc))


@router.post("/_admin/migrate")
def force_migrate():
    try:
        run_moment_migrations(force=True)
        return {"success": True, "message": "Migration re-run complete"}
    except Exception as exc:
        logger.exception("force migrate failed")
        raise HTTPException(500, str(exc))


@router.get("/audience-users")
async def audience_users(
    request: Request,
    type: Literal["connections", "following", "followers"] = Query("connections"),
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)
    try:
        rows = db.execute(text("""
            WITH me AS (SELECT id, user_id FROM public.users WHERE user_id = :u)
            SELECT DISTINCT u.user_id, u.name, u.username, u.profile_photo
              FROM public.users u CROSS JOIN me
             WHERE u.id != me.id AND (
               (:t = 'connections' AND EXISTS (
                  SELECT 1 FROM public.user_connections c
                   WHERE c.status = 'connected'
                     AND ((c.user_one_id = me.id AND c.user_two_id = u.id)
                       OR (c.user_two_id = me.id AND c.user_one_id = u.id))))
               OR (:t = 'following' AND EXISTS (
                  SELECT 1 FROM public.user_follows f
                   WHERE f.follower_id = me.id AND f.following_id = u.id))
               OR (:t = 'followers' AND EXISTS (
                  SELECT 1 FROM public.user_follows f
                   WHERE f.follower_id = u.id AND f.following_id = me.id))
             )
             ORDER BY u.name ASC
        """), {"u": user.user_id, "t": type}).mappings().all()
        return {"success": True, "type": type, "count": len(rows),
                "users": [dict(r) for r in rows]}
    except Exception:
        logger.exception("audience users failed")
        raise HTTPException(500, "Unable to fetch audience users")


@router.post("/create")
async def create_moment(payload: MomentCreateBody, request: Request,
                        db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    selected = _validate_audience(payload.audience, payload.moment_scope,
                                  payload.selected_user_ids)

    if payload.moment_type == "gif":
        if not payload.gif_url:
            raise HTTPException(400, "gif_url is required for GIF moment")
    if payload.moment_type == "countdown":
        if not payload.metadata.get("target_date"):
            raise HTTPException(400, "metadata.target_date required")
    if payload.moment_type == "emoji_slider":
        if not payload.metadata.get("emoji"):
            raise HTTPException(400, "metadata.emoji required")

    try:
        row = db.execute(text("""
            INSERT INTO nex_moments
              (user_id, moment_type, caption, media_url, audience,
               moment_scope, moderation_status, metadata, created_at, expires_at)
            VALUES
              (:u, :t, :c, :m, :a, :s, 'active', CAST(:meta AS jsonb),
               NOW(), NOW() + INTERVAL '24 hours')
            RETURNING id, created_at, expires_at
        """), {
            "u": user.user_id, "t": payload.moment_type,
            "c": payload.caption.strip(),
            "m": payload.gif_url if payload.moment_type == "gif" else None,
            "a": payload.audience, "s": payload.moment_scope,
            "meta": json.dumps(payload.metadata or {}),
        }).mappings().first()

        if not row:
            raise RuntimeError("insert returned no row")

        _save_audience(db, row["id"], selected, user.user_id)
        _audit(db, row["id"], user.user_id, "moment_created")
        db.commit()

        fresh = _get_moment_row(db, row["id"])
        return {"success": True, "moment": _serialize_moment(db, fresh, user.user_id)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); logger.exception("create moment failed")
        raise HTTPException(500, "Moment creation failed")


@router.post("/upload")
async def upload_moment(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    audience: Literal[
        "connections", "following", "followers", "selected_people", "only_me"
    ] = Form("connections"),
    moment_scope: Literal["normal"] = Form("normal"),
    selected_user_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)
    selected = _validate_audience(audience, moment_scope, selected_user_ids)

    storage_uploaded, storage_path = False, None
    try:
        if len(caption) > 2000:
            raise HTTPException(400, "Caption exceeds 2000 chars")

        data = await file.read(MAX_MEDIA + 1)
        if not data:
            raise HTTPException(400, "Empty file")
        if len(data) > MAX_MEDIA:
            raise HTTPException(413, "Max 50 MB")

        mime = _detect_mime(data)
        if mime is None or mime not in ALLOWED_MEDIA:
            raise HTTPException(415, "Unsupported media")

        # ⬇⬇ FIXED: single-level indentation (was accidentally indented too far)
        media_type, ext = ALLOWED_MEDIA[mime]
        storage_path = f"{user.user_id}/{uuid.uuid4().hex}{ext}"

        # ⬇ capture the returned URL (Supabase key OR /static/... path)
        media_url = upload_moment_media(
            file_bytes=data,
            file_path=storage_path,
            content_type=mime,
        )
        storage_uploaded = True

        row = db.execute(text("""
            INSERT INTO nex_moments
              (user_id, moment_type, caption, media_url, audience,
               moment_scope, moderation_status, created_at, expires_at)
            VALUES (:u, :t, :c, :m, :a, 'normal', 'active',
                    NOW(), NOW() + INTERVAL '24 hours')
            RETURNING id
        """), {"u": user.user_id, "t": media_type, "c": caption.strip(),
               "m": media_url, "a": audience}).mappings().first()

        if not row:
            raise RuntimeError("insert failed")
        mid = row["id"]

        db.execute(text("""
            INSERT INTO nex_moment_media
                (moment_id, uploader_user_id, media_type, storage_path,
                 original_file_name, mime_type, file_size_bytes)
            VALUES (:m, :u, :t, :p, :n, :mt, :sz)
        """), {"m": mid, "u": user.user_id, "t": media_type, "p": storage_path,
               "n": Path((file.filename or "upload").replace("\\", "/")).name[:255],
               "mt": mime, "sz": len(data)})

        _save_audience(db, mid, selected, user.user_id)
        _audit(db, mid, user.user_id, "moment_uploaded")
        db.commit()

        fresh = _get_moment_row(db, mid)
        return {"success": True, "moment": _serialize_moment(db, fresh, user.user_id)}
    except HTTPException:
        db.rollback()
        if storage_uploaded and storage_path:
            try:
                delete_moment_media(storage_path)
            except Exception:
                pass
        raise
    except Exception:
        db.rollback(); logger.exception("upload moment failed")
        if storage_uploaded and storage_path:
            try:
                delete_moment_media(storage_path)
            except Exception:
                pass
        raise HTTPException(500, "Moment upload failed")
    finally:
        await file.close()


@router.get("/feed")
async def feed(request: Request, limit: int = 50,
               db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    if limit < 1 or limit > 100:
        raise HTTPException(400, "Limit 1-100")
    try:
        rows = db.execute(text("""
            SELECT * FROM nex_moments
             WHERE expires_at > NOW()
               AND archived_at IS NULL AND deleted_at IS NULL
               AND moderation_status = 'active'
               AND (user_id = :v
                    OR (moment_scope = 'normal'
                        AND can_view_normal_moment(id, :v))
                    OR (moment_scope = 'couple'
                        AND can_view_couple_moment(id, :v)))
             ORDER BY created_at DESC LIMIT :l
        """), {"v": user.user_id, "l": limit}).mappings().all()
        return {"success": True, "count": len(rows),
                "moments": [_serialize_moment(db, r, user.user_id) for r in rows]}
    except Exception:
        logger.exception("feed failed")
        raise HTTPException(500, "Unable to fetch moments")


@router.get("/story-row")
async def story_row(request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    try:
        rows = db.execute(text("""
            SELECT * FROM nex_moments
             WHERE expires_at > NOW()
               AND archived_at IS NULL AND deleted_at IS NULL
               AND moderation_status = 'active'
               AND (user_id = :v
                    OR (moment_scope = 'normal'
                        AND can_view_normal_moment(id, :v)))
             ORDER BY created_at ASC
        """), {"v": user.user_id}).mappings().all()

        grouped = {}
        for r in rows:
            grouped.setdefault(r["user_id"], []).append(
                _serialize_moment(db, r, user.user_id)
            )

        uids = list(grouped.keys())
        creators = {}
        if uids:
            cr = db.execute(text("""
                SELECT user_id, name, username, profile_photo
                  FROM public.users WHERE user_id = ANY(:u)
            """), {"u": uids}).mappings().all()
            creators = {c["user_id"]: dict(c) for c in cr}

        out = []
        for uid, moments in grouped.items():
            creator = creators.get(uid, {"user_id": uid})
            out.append({
                "user_id": uid,
                "name": creator.get("name"),
                "username": creator.get("username"),
                "profile_photo": creator.get("profile_photo"),
                "is_mine": uid == user.user_id,
                "has_unviewed": any(not m["viewed"] for m in moments),
                "moments": moments,
            })

        out.sort(key=lambda g: (not g["is_mine"], not g["has_unviewed"]))
        return {"success": True, "groups": out}
    except Exception:
        logger.exception("story-row failed")
        raise HTTPException(500, "Unable to fetch")


@router.get("/archive")
async def archive(request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    rows = db.execute(text("""
        SELECT * FROM nex_moments
         WHERE user_id = :u AND archived_at IS NOT NULL AND deleted_at IS NULL
         ORDER BY archived_at DESC
    """), {"u": user.user_id}).mappings().all()
    return {"success": True,
            "moments": [_serialize_moment(db, r, user.user_id) for r in rows]}


@router.get("/mine")
async def mine(request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    rows = db.execute(text("""
        SELECT * FROM nex_moments
         WHERE user_id = :u AND deleted_at IS NULL AND archived_at IS NULL
           AND expires_at > NOW()
         ORDER BY created_at DESC
    """), {"u": user.user_id}).mappings().all()
    return {"success": True,
            "moments": [_serialize_moment(db, r, user.user_id) for r in rows]}


@router.get("/settings")
async def get_settings(request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = db.execute(text("""
        SELECT default_audience, comments_enabled, reactions_enabled,
               viewers_visible, notifications_enabled
          FROM nex_moment_settings WHERE user_id = :u
    """), {"u": user.user_id}).mappings().first()

    if not row:
        return {"success": True, "settings": {
            "default_audience": "connections",
            "comments_enabled": True,
            "reactions_enabled": True,
            "viewers_visible": True,
            "notifications_enabled": True,
        }}
    return {"success": True, "settings": dict(row)}


@router.put("/settings")
async def update_settings(payload: SettingsBody, request: Request,
                          db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    current = db.execute(text(
        "SELECT * FROM nex_moment_settings WHERE user_id = :u"
    ), {"u": user.user_id}).mappings().first()

    data = dict(current) if current else {
        "default_audience": "connections", "comments_enabled": True,
        "reactions_enabled": True, "viewers_visible": True,
        "notifications_enabled": True,
    }
    for k, v in payload.dict(exclude_unset=True).items():
        if v is not None:
            data[k] = v

    db.execute(text("""
        INSERT INTO nex_moment_settings
            (user_id, default_audience, comments_enabled, reactions_enabled,
             viewers_visible, notifications_enabled, updated_at)
        VALUES (:u, :da, :ce, :re, :vv, :ne, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            default_audience      = EXCLUDED.default_audience,
            comments_enabled      = EXCLUDED.comments_enabled,
            reactions_enabled     = EXCLUDED.reactions_enabled,
            viewers_visible       = EXCLUDED.viewers_visible,
            notifications_enabled = EXCLUDED.notifications_enabled,
            updated_at            = NOW()
    """), {"u": user.user_id,
           "da": data["default_audience"],
           "ce": data["comments_enabled"],
           "re": data["reactions_enabled"],
           "vv": data["viewers_visible"],
           "ne": data["notifications_enabled"]})
    db.commit()
    return {"success": True, "settings": data}


@router.get("/notifications/list")
async def moment_notifications(request: Request, limit: int = 50,
                               db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    rows = db.execute(text("""
        SELECT n.id, n.actor_user_id, n.moment_id, n.notification_type,
               n.message, n.is_read, n.created_at,
               u.name AS actor_name, u.username AS actor_username,
               u.profile_photo AS actor_photo
          FROM nex_moment_notifications n
          LEFT JOIN public.users u ON u.user_id = n.actor_user_id
         WHERE n.recipient_user_id = :u
         ORDER BY n.created_at DESC LIMIT :l
    """), {"u": user.user_id, "l": min(limit, 100)}).mappings().all()
    return {"success": True, "notifications": [
        {**dict(r),
         "created_at": r["created_at"].isoformat() if r["created_at"] else None}
        for r in rows
    ]}


@router.post("/notifications/read")
async def read_notification(request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    nid = body.get("notification_id")
    if not nid:
        raise HTTPException(400, "notification_id required")
    db.execute(text("""
        UPDATE nex_moment_notifications SET is_read = TRUE
         WHERE id = :id AND recipient_user_id = :u
    """), {"id": nid, "u": user.user_id})
    db.commit()
    return {"success": True}


# =========================================================
# ⚠️  DYNAMIC ROUTES LAST — /{moment_id} catch-all
# =========================================================
@router.get("/{moment_id}")
async def get_one(moment_id: int, request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)

    if not row or row["deleted_at"]:
        raise HTTPException(404, "Moment not found")

    if row["moment_scope"] == "normal" and row["archived_at"]:
        if row["user_id"] != user.user_id:
            raise HTTPException(404, "Moment not found")

    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")

    return {
        "success": True,
        "moment": _serialize_moment(db, row, user.user_id)
    }

@router.put("/{moment_id}")
async def update_moment(moment_id: int, payload: MomentUpdateBody,
                        request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(403, "Not your moment")

    sel = None
    if payload.audience or payload.selected_user_ids is not None:
        new_aud = payload.audience or row["audience"]
        sel = _validate_audience(new_aud, row["moment_scope"],
                                 payload.selected_user_ids or [])

    try:
        db.execute(text("""
            UPDATE nex_moments SET
              caption = COALESCE(:c, caption),
              audience = COALESCE(:a, audience),
              metadata = COALESCE(CAST(:meta AS jsonb), metadata)
             WHERE id = :id
        """), {
            "c": payload.caption.strip() if payload.caption is not None else None,
            "a": payload.audience,
            "meta": json.dumps(payload.metadata) if payload.metadata is not None else None,
            "id": moment_id,
        })
        if sel is not None:
            _save_audience(db, moment_id, sel, user.user_id)
        _audit(db, moment_id, user.user_id, "moment_updated")
        db.commit()
        fresh = _get_moment_row(db, moment_id)
        return {"success": True,
                "moment": _serialize_moment(db, fresh, user.user_id)}
    except HTTPException:
        db.rollback(); raise
    except Exception:
        db.rollback(); raise HTTPException(500, "Update failed")


@router.delete("/{moment_id}")
async def delete_moment(moment_id: int, request: Request,
                        db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(403, "Not your moment")
    try:
        db.execute(text("UPDATE nex_moments SET deleted_at = NOW() WHERE id = :id"),
                   {"id": moment_id})
        _audit(db, moment_id, user.user_id, "moment_deleted")
        db.commit()
        return {"success": True, "moment_id": moment_id}
    except Exception:
        db.rollback(); raise HTTPException(500, "Delete failed")


@router.post("/{moment_id}/archive")
async def archive_moment(moment_id: int, request: Request,
                         db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(403, "Not your moment")
    if row["archived_at"]:
        return {"success": True, "already_archived": True}
    db.execute(text("UPDATE nex_moments SET archived_at = NOW() WHERE id = :id"),
               {"id": moment_id})
    _audit(db, moment_id, user.user_id, "moment_archived")
    db.commit()
    return {"success": True, "moment_id": moment_id, "archived": True}
def _moment_media_exists(db: Session, moment_id: int) -> bool:
    media = db.execute(
        text("""
            SELECT storage_path, thumbnail_path
            FROM nex_moment_media
            WHERE moment_id = :moment_id
        """),
        {"moment_id": moment_id},
    ).mappings().first()

    # Text-only Moment ke liye media required nahi hai.
    if not media:
        return True

    paths = [
        path for path in (
            media["storage_path"],
            media["thumbnail_path"],
        ) if path
    ]

    if not paths:
        return False

    from ..services.moment_cleanup import storage_file_exists

    bucket = get_supabase_client().storage.from_("nex-moments")

    return all(
        storage_file_exists(bucket, path)
        for path in paths
    )

@router.post("/{moment_id}/restore")
async def restore_moment(moment_id: int, request: Request,
                         db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)

    row = _get_moment_row(db, moment_id)

    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")

    if row["user_id"] != user.user_id:
        raise HTTPException(403, "Not your moment")

    # Check expiry using database time.
    expired = db.execute(
        text("""
            SELECT expires_at IS NULL OR expires_at <= NOW()
            FROM nex_moments
            WHERE id = :id
        """),
        {"id": moment_id}
    ).scalar()

    if expired:
        raise HTTPException(
            400,
            "Expired Moment cannot be restored"
        )

    # Check whether uploaded media still exists.
    if not _moment_media_exists(db, moment_id):
        raise HTTPException(
            400,
            "Moment media is missing and cannot be restored"
        )

    db.execute(
        text("""
            UPDATE nex_moments
            SET archived_at = NULL
            WHERE id = :id
        """),
        {"id": moment_id}
    )

    _audit(db, moment_id, user.user_id, "moment_restored")
    db.commit()

    return {
        "success": True,
        "moment_id": moment_id,
        "restored": True
    }


@router.post("/{moment_id}/view")
async def mark_viewed(moment_id: int, request: Request,
                      db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")
    db.execute(text("""
        INSERT INTO nex_moment_views (moment_id, viewer_user_id, viewed_at)
        VALUES (:m, :v, NOW())
        ON CONFLICT (moment_id, viewer_user_id) DO NOTHING
    """), {"m": moment_id, "v": user.user_id})
    db.commit()
    return {"success": True}


@router.get("/{moment_id}/viewers")
async def viewers(moment_id: int, request: Request,
                  db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(403, "Only owner can view viewer list")

    pref = db.execute(text(
        "SELECT viewers_visible FROM nex_moment_settings WHERE user_id = :u"
    ), {"u": user.user_id}).scalar()
    if pref is False:
        raise HTTPException(403, "Viewer list disabled in your settings")

    rows = db.execute(text("""
        SELECT v.viewer_user_id AS user_id, u.name, u.username,
               u.profile_photo, v.viewed_at
          FROM nex_moment_views v
          LEFT JOIN public.users u ON u.user_id = v.viewer_user_id
         WHERE v.moment_id = :m
         ORDER BY v.viewed_at DESC
    """), {"m": moment_id}).mappings().all()
    return {"success": True,
            "viewers": [{**dict(r),
                         "viewed_at": r["viewed_at"].isoformat() if r["viewed_at"] else None}
                        for r in rows]}


@router.get("/{moment_id}/views-count")
async def views_count(moment_id: int, request: Request,
                      db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")
    c = db.execute(text(
        "SELECT COUNT(*) FROM nex_moment_views WHERE moment_id = :m"
    ), {"m": moment_id}).scalar() or 0
    return {"success": True, "count": int(c)}


@router.post("/{moment_id}/reactions")
async def add_reaction(moment_id: int, payload: ReactionBody,
                       request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")
    if payload.reaction not in VALID_REACTIONS:
        raise HTTPException(400, f"Reaction must be one of {sorted(VALID_REACTIONS)}")

    creator_pref = db.execute(text(
        "SELECT reactions_enabled FROM nex_moment_settings WHERE user_id = :u"
    ), {"u": row["user_id"]}).scalar()
    if creator_pref is False:
        raise HTTPException(403, "Reactions disabled by creator")

    try:
        db.execute(text("""
            INSERT INTO nex_moment_reactions (moment_id, user_id, reaction)
            VALUES (:m, :u, :r)
            ON CONFLICT (moment_id, user_id)
            DO UPDATE SET reaction = EXCLUDED.reaction, created_at = NOW()
        """), {"m": moment_id, "u": user.user_id, "r": payload.reaction})
        _notify(db, row["user_id"], user.user_id, moment_id,
                "reaction", f"{payload.reaction} on your moment")
        db.commit()
        return {"success": True, "reaction": payload.reaction}
    except Exception:
        db.rollback(); raise HTTPException(500, "Reaction failed")


@router.delete("/{moment_id}/reactions")
async def remove_reaction(moment_id: int, request: Request,
                          db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    db.execute(text(
        "DELETE FROM nex_moment_reactions WHERE moment_id = :m AND user_id = :u"
    ), {"m": moment_id, "u": user.user_id})
    db.commit()
    return {"success": True}


@router.get("/{moment_id}/reactions")
async def list_reactions(moment_id: int, request: Request,
                         db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")
    rows = db.execute(text("""
        SELECT r.user_id, r.reaction, u.name, u.username, u.profile_photo
          FROM nex_moment_reactions r
          LEFT JOIN public.users u ON u.user_id = r.user_id
         WHERE r.moment_id = :m
    """), {"m": moment_id}).mappings().all()

    counts = {}
    for r in rows:
        counts[r["reaction"]] = counts.get(r["reaction"], 0) + 1
    my = next((r["reaction"] for r in rows if r["user_id"] == user.user_id), None)
    return {"success": True, "counts": counts, "my_reaction": my,
            "reactions": [dict(r) for r in rows]}


@router.get("/{moment_id}/comments")
async def list_comments(moment_id: int, request: Request,
                        limit: int = 100, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")

    rows = db.execute(text("""
        SELECT c.id, c.user_id, c.content, c.parent_comment_id,
               c.created_at, u.name, u.username, u.profile_photo
          FROM nex_moment_comments c
          LEFT JOIN public.users u ON u.user_id = c.user_id
         WHERE c.moment_id = :m AND c.is_deleted = FALSE
         ORDER BY c.created_at DESC LIMIT :l
    """), {"m": moment_id, "l": min(limit, 200)}).mappings().all()
    return {"success": True,
            "comments": [{**dict(r),
                          "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                          "is_owner": r["user_id"] == user.user_id}
                         for r in rows]}


@router.post("/{moment_id}/comments")
async def add_comment(moment_id: int, payload: CommentBody,
                      request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")

    creator_pref = db.execute(text(
        "SELECT comments_enabled FROM nex_moment_settings WHERE user_id = :u"
    ), {"u": row["user_id"]}).scalar()
    if creator_pref is False:
        raise HTTPException(403, "Comments disabled by creator")

    try:
        res = db.execute(text("""
            INSERT INTO nex_moment_comments
              (moment_id, user_id, content, parent_comment_id, created_at, updated_at)
            VALUES (:m, :u, :c, :p, NOW(), NOW())
            RETURNING id, created_at
        """), {"m": moment_id, "u": user.user_id,
               "c": payload.content.strip(),
               "p": payload.parent_comment_id}).mappings().first()

        _notify(db, row["user_id"], user.user_id, moment_id,
                "comment", payload.content.strip()[:100])
        db.commit()
        return {"success": True, "comment": {
            "id": res["id"], "user_id": user.user_id,
            "content": payload.content.strip(),
            "created_at": res["created_at"].isoformat(),
            "name": user.name, "username": user.username,
            "profile_photo": user.profile_photo, "is_owner": True,
        }}
    except Exception:
        db.rollback(); raise HTTPException(500, "Comment failed")


@router.delete("/{moment_id}/comments/{comment_id}")
async def delete_comment(moment_id: int, comment_id: int,
                         request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")

    c = db.execute(text("""
        SELECT id, user_id FROM nex_moment_comments
         WHERE id = :c AND moment_id = :m AND is_deleted = FALSE
    """), {"c": comment_id, "m": moment_id}).mappings().first()
    if not c:
        raise HTTPException(404, "Comment not found")

    if c["user_id"] != user.user_id and row["user_id"] != user.user_id:
        raise HTTPException(403, "Not allowed")

    db.execute(text(
        "UPDATE nex_moment_comments SET is_deleted = TRUE WHERE id = :c"
    ), {"c": comment_id})
    db.commit()
    return {"success": True}


@router.post("/{moment_id}/report")
async def report_moment(moment_id: int, payload: ReportBody,
                        request: Request, db: Session = Depends(get_db)):
    user = await get_authenticated_user(request, db)
    row = _get_moment_row(db, moment_id)
    if not row or row["deleted_at"]:
        raise HTTPException(404, "Not found")
    if not _can_view(db, moment_id, user.user_id, row["moment_scope"]):
        raise HTTPException(403, "Not allowed")
    db.execute(text("""
        INSERT INTO nex_moment_reports
            (moment_id, reporter_user_id, reason, description)
        VALUES (:m, :u, :r, :d)
    """), {"m": moment_id, "u": user.user_id,
           "r": payload.reason.strip(),
           "d": payload.description.strip() if payload.description else None})
    db.commit()
    return {"success": True, "message": "Report submitted"}
