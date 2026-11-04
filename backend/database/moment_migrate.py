# backend/database/moment_migrate.py
"""
Idempotent moment schema migration.
- Safe to call multiple times.
- Adds missing columns to existing tables.
"""
import logging
from threading import Lock
from sqlalchemy import text, inspect
from .database import engine

logger = logging.getLogger(__name__)

# ── DDL — one statement per list entry so we can retry individually ─────
_DDL = [
    # ── core table ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moments (
        id BIGSERIAL PRIMARY KEY,
        user_id VARCHAR(30) NOT NULL,
        moment_type VARCHAR(30) NOT NULL DEFAULT 'text',
        caption TEXT,
        media_url TEXT,
        audience VARCHAR(30) NOT NULL DEFAULT 'connections',
        moment_scope VARCHAR(20) NOT NULL DEFAULT 'normal',
        moderation_status VARCHAR(20) NOT NULL DEFAULT 'active',
        admin_moderation_reason TEXT,
        admin_hidden_at TIMESTAMPTZ,
        admin_hidden_by VARCHAR(30),
        archived_at TIMESTAMPTZ,
        deleted_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS audience VARCHAR(30) NOT NULL DEFAULT 'connections'",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS moment_scope VARCHAR(20) NOT NULL DEFAULT 'normal'",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS admin_moderation_reason TEXT",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS admin_hidden_at TIMESTAMPTZ",
    "ALTER TABLE nex_moments ADD COLUMN IF NOT EXISTS admin_hidden_by VARCHAR(30)",

    # ── indexes on core ──────────────────────────────────
    "CREATE INDEX IF NOT EXISTS ix_nex_moments_user     ON nex_moments(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_nex_moments_created  ON nex_moments(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_nex_moments_expires  ON nex_moments(expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_nex_moments_scope    ON nex_moments(moment_scope)",

    # ── audience ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_audience (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        allowed_user_id VARCHAR(30) NOT NULL,
        UNIQUE (moment_id, allowed_user_id)
    )
    """,
    "ALTER TABLE nex_moment_audience ADD COLUMN IF NOT EXISTS allowed_user_id VARCHAR(30)",
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_audience_m ON nex_moment_audience(moment_id)",

    # ── media metadata ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_media (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        uploader_user_id VARCHAR(30) NOT NULL,
        media_type VARCHAR(20) NOT NULL,
        storage_path TEXT NOT NULL,
        original_file_name VARCHAR(255),
        mime_type VARCHAR(100),
        file_size_bytes BIGINT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE nex_moment_media ADD COLUMN IF NOT EXISTS uploader_user_id VARCHAR(30)",
    "ALTER TABLE nex_moment_media ADD COLUMN IF NOT EXISTS media_type VARCHAR(20)",
    "ALTER TABLE nex_moment_media ADD COLUMN IF NOT EXISTS storage_path TEXT",

    # ── audit ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_audit_logs (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        user_id VARCHAR(30) NOT NULL,
        action VARCHAR(50) NOT NULL,
        old_data JSONB,
        new_data JSONB,
        action_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_audit_m ON nex_moment_audit_logs(moment_id)",

    # ── views ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_views (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        viewer_user_id VARCHAR(30) NOT NULL,
        viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (moment_id, viewer_user_id)
    )
    """,
    "ALTER TABLE nex_moment_views ADD COLUMN IF NOT EXISTS viewer_user_id VARCHAR(30)",
    "ALTER TABLE nex_moment_views ADD COLUMN IF NOT EXISTS viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_views_m ON nex_moment_views(moment_id)",

    # ── reactions ────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_reactions (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        user_id VARCHAR(30) NOT NULL,
        reaction VARCHAR(30) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (moment_id, user_id)
    )
    """,
    # 🚨 YEH LINE FIX HAI: Agar purane table mein 'reaction' column nahi hai, toh add karega
    "ALTER TABLE nex_moment_reactions ADD COLUMN IF NOT EXISTS reaction VARCHAR(30)",
    "ALTER TABLE nex_moment_reactions ADD COLUMN IF NOT EXISTS user_id VARCHAR(30)",
    "ALTER TABLE nex_moment_reactions ADD COLUMN IF NOT EXISTS moment_id BIGINT",
    "ALTER TABLE nex_moment_reactions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_reactions_m ON nex_moment_reactions(moment_id)",

    # ── comments ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_comments (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        user_id VARCHAR(30) NOT NULL,
        content TEXT NOT NULL,
        parent_comment_id BIGINT,
        is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE nex_moment_comments ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE nex_moment_comments ADD COLUMN IF NOT EXISTS parent_comment_id BIGINT",
    "ALTER TABLE nex_moment_comments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_comments_m ON nex_moment_comments(moment_id, created_at DESC)",

    # ── reports ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_reports (
        id BIGSERIAL PRIMARY KEY,
        moment_id BIGINT NOT NULL,
        reporter_user_id VARCHAR(30) NOT NULL,
        reason VARCHAR(100) NOT NULL,
        description TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE nex_moment_reports ADD COLUMN IF NOT EXISTS reporter_user_id VARCHAR(30)",
    "ALTER TABLE nex_moment_reports ADD COLUMN IF NOT EXISTS reason VARCHAR(100)",

    # ── settings ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_settings (
        user_id VARCHAR(30) PRIMARY KEY,
        default_audience VARCHAR(30) NOT NULL DEFAULT 'connections',
        comments_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        viewers_visible BOOLEAN NOT NULL DEFAULT TRUE,
        notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE nex_moment_settings ADD COLUMN IF NOT EXISTS comments_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE nex_moment_settings ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE nex_moment_settings ADD COLUMN IF NOT EXISTS viewers_visible BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE nex_moment_settings ADD COLUMN IF NOT EXISTS notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE",

    # ── notifications ────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nex_moment_notifications (
        id BIGSERIAL PRIMARY KEY,
        recipient_user_id VARCHAR(30) NOT NULL,
        actor_user_id VARCHAR(30) NOT NULL,
        moment_id BIGINT NOT NULL,
        notification_type VARCHAR(30) NOT NULL,
        message TEXT,
        is_read BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nex_moment_notif_r ON nex_moment_notifications(recipient_user_id, created_at DESC)",
]

# ── functions — must run AFTER the tables above exist ─────
_FUNCTIONS = [
    """
    CREATE OR REPLACE FUNCTION can_view_normal_moment(
        p_moment_id BIGINT, p_viewer_user_id VARCHAR(30)
    ) RETURNS BOOLEAN AS $$
    DECLARE v_creator VARCHAR(30); v_aud VARCHAR(30);
    BEGIN
        SELECT user_id, audience INTO v_creator, v_aud
          FROM nex_moments
         WHERE id = p_moment_id AND moment_scope = 'normal'
           AND deleted_at IS NULL AND archived_at IS NULL
           AND moderation_status = 'active';
        IF v_creator IS NULL THEN RETURN FALSE; END IF;
        IF v_creator = p_viewer_user_id THEN RETURN TRUE; END IF;
        IF v_aud = 'only_me' THEN RETURN FALSE; END IF;
        IF v_aud IN ('connections','following','followers','public') THEN
            RETURN TRUE;
        END IF;
        IF v_aud = 'selected_people' THEN
            RETURN EXISTS (
                SELECT 1 FROM nex_moment_audience
                 WHERE moment_id = p_moment_id
                   AND allowed_user_id = p_viewer_user_id
            );
        END IF;
        RETURN FALSE;
    END; $$ LANGUAGE plpgsql;
    """,
    """
    CREATE OR REPLACE FUNCTION can_view_couple_moment(
        p_moment_id BIGINT, p_viewer_user_id VARCHAR(30)
    ) RETURNS BOOLEAN AS $$
    BEGIN
        RETURN EXISTS (
            SELECT 1 FROM nex_moments
             WHERE id = p_moment_id AND moment_scope = 'couple'
               AND user_id = p_viewer_user_id
               AND deleted_at IS NULL AND archived_at IS NULL
        );
    END; $$ LANGUAGE plpgsql;
    """,
]

_ensured = False
_lock = Lock()

def _tables_ok(conn) -> bool:
    try:
        insp = inspect(conn)
        return insp.has_table("nex_moments")
    except Exception:
        return False

def run_moment_migrations(force: bool = False) -> None:
    """
    Idempotent. Safe to call from every request.
    First call executes DDL; subsequent calls are a no-op.
    """
    global _ensured

    if _ensured and not force:
        return

    with _lock:
        if _ensured and not force:
            return

        errors = []

        # Phase 1: create tables + ALTER columns
        for i, stmt in enumerate(_DDL):
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                logger.warning(
                    "[Usanex Moments] DDL step %d failed: %s", i, exc
                )
                errors.append((i, str(exc)))

        # Phase 2: verify table exists before creating functions
        with engine.connect() as conn:
            if not _tables_ok(conn):
                logger.error(
                    "[Usanex Moments] nex_moments table still missing after DDL. "
                    "Check DATABASE_URL / permissions."
                )
                raise RuntimeError(
                    "nex_moments table could not be created. "
                    "Check the DB connection and permissions."
                )

        # Phase 3: create functions
        for i, stmt in enumerate(_FUNCTIONS):
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                logger.error(
                    "[Usanex Moments] function DDL step %d failed: %s", i, exc
                )
                raise

        _ensured = True
        if errors:
            logger.info(
                "[Usanex Moments] Schema ensured with %d non-fatal warnings",
                len(errors),
            )
        else:
            logger.info("[Usanex Moments] Schema ensured successfully")