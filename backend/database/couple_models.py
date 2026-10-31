# =========================================================
# USANEX — COUPLE CHAT DATABASE MODELS
# backend/database/couple_models.py
# =========================================================

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .database import Base


# =========================================================
# TIME HELPER
# =========================================================

def utcnow():
    """
    Current UTC time as naive datetime.
    """
    return datetime.utcnow()


# =========================================================
# COUPLE ROOM
# =========================================================

class CoupleRoom(Base):

    __tablename__ = "couple_rooms"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_one_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    user_two_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    relationship_started_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    __table_args__ = (

        UniqueConstraint(
            "user_one_id",
            "user_two_id",
            name="uq_couple_room_users",
        ),

        Index(
            "ix_couple_room_user_one_two",
            "user_one_id",
            "user_two_id",
        ),
    )


# =========================================================
# COUPLE MESSAGE
# =========================================================

class CoupleMessage(Base):

    __tablename__ = "couple_messages"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    room_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    sender_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    receiver_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    # -----------------------------------------------------
    # Message content
    # -----------------------------------------------------

    content = Column(
        Text,
        nullable=True,
    )

    # -----------------------------------------------------
    # Media
    # -----------------------------------------------------

    media_url = Column(
        String(1000),
        nullable=True,
    )

    media_type = Column(
        String(30),
        nullable=True,
    )

    # -----------------------------------------------------
    # Message type
    # -----------------------------------------------------

    message_type = Column(
        String(30),
        nullable=False,
        default="text",
    )

    # -----------------------------------------------------
    # Reply
    # -----------------------------------------------------

    reply_to_message_id = Column(
        Integer,
        nullable=True,
        index=True,
    )

    # -----------------------------------------------------
    # Message state
    # -----------------------------------------------------

    is_deleted = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_edited = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -----------------------------------------------------
    # AI metadata
    # -----------------------------------------------------

    ai_mode = Column(
        String(50),
        nullable=True,
    )

    ai_emotion = Column(
        String(50),
        nullable=True,
    )

    ai_intent = Column(
        String(50),
        nullable=True,
    )

    ai_intensity = Column(
        Float,
        nullable=True,
    )

    ai_theme = Column(
        String(50),
        nullable=True,
    )

    # -----------------------------------------------------
    # Timestamps
    # -----------------------------------------------------

    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    __table_args__ = (

        Index(
            "ix_couple_message_room_created",
            "room_id",
            "created_at",
        ),

        Index(
            "ix_couple_message_sender_created",
            "sender_id",
            "created_at",
        ),

        Index(
            "ix_couple_message_receiver_created",
            "receiver_id",
            "created_at",
        ),
    )


# =========================================================
# MESSAGE RECEIPT
# =========================================================

class CoupleMessageReceipt(Base):

    __tablename__ = "couple_message_receipts"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    message_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    receiver_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    # -----------------------------------------------------
    # Delivered
    # -----------------------------------------------------

    delivered = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    delivered_at = Column(
        DateTime,
        nullable=True,
    )

    # -----------------------------------------------------
    # Seen
    # -----------------------------------------------------

    seen = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    seen_at = Column(
        DateTime,
        nullable=True,
    )

    # -----------------------------------------------------
    # Timestamps
    # -----------------------------------------------------

    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    __table_args__ = (

        UniqueConstraint(
            "message_id",
            "receiver_id",
            name="uq_couple_message_receipt",
        ),

        Index(
            "ix_couple_receipt_message_receiver",
            "message_id",
            "receiver_id",
        ),
    )


# =========================================================
# COUPLE PRESENCE
# =========================================================

class CouplePresence(Base):

    __tablename__ = "couple_presence"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id = Column(
        Integer,
        nullable=False,
        unique=True,
        index=True,
    )

    is_online = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    last_seen_at = Column(
        DateTime,
        nullable=True,
    )

    last_online_at = Column(
        DateTime,
        nullable=True,
    )

    current_room_id = Column(
        Integer,
        nullable=True,
        index=True,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )


# =========================================================
# COUPLE MEMORY
# =========================================================

class CoupleMemory(Base):

    __tablename__ = "couple_memories"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    room_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    created_by_user_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    memory_type = Column(
        String(50),
        nullable=False,
        default="general",
    )

    title = Column(
        String(255),
        nullable=True,
    )

    content = Column(
        Text,
        nullable=False,
    )

    importance = Column(
        Float,
        default=0.5,
        nullable=False,
    )

    ai_generated = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
    )

    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    __table_args__ = (

        Index(
            "ix_couple_memory_room_active",
            "room_id",
            "is_active",
        ),
    )


# =========================================================
# COUPLE AI EVENT
# =========================================================

class CoupleAIEvent(Base):

    __tablename__ = "couple_ai_events"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    room_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    message_id = Column(
        Integer,
        nullable=True,
        index=True,
    )

    user_id = Column(
        Integer,
        nullable=False,
        index=True,
    )

    # -----------------------------------------------------
    # AI analysis
    # -----------------------------------------------------

    mode = Column(
        String(50),
        nullable=True,
    )

    secondary_mode = Column(
        String(50),
        nullable=True,
    )

    emotion = Column(
        String(50),
        nullable=True,
    )

    intent = Column(
        String(50),
        nullable=True,
    )

    intensity = Column(
        Float,
        nullable=True,
    )

    # -----------------------------------------------------
    # UI decision
    # -----------------------------------------------------

    theme = Column(
        String(50),
        nullable=True,
    )

    animation = Column(
        String(50),
        nullable=True,
    )

    response_style = Column(
        String(50),
        nullable=True,
    )

    # -----------------------------------------------------
    # AI response
    # -----------------------------------------------------

    ai_response = Column(
        Text,
        nullable=True,
    )

    # -----------------------------------------------------
    # Provider metadata
    # -----------------------------------------------------

    provider = Column(
        String(50),
        nullable=True,
    )

    model = Column(
        String(100),
        nullable=True,
    )

    processing_time_ms = Column(
        Integer,
        nullable=True,
    )

    # -----------------------------------------------------
    # Result
    # -----------------------------------------------------

    success = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    error_message = Column(
        Text,
        nullable=True,
    )

    # -----------------------------------------------------
    # Timestamp
    # -----------------------------------------------------

    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
    )

    __table_args__ = (

        Index(
            "ix_couple_ai_event_room_created",
            "room_id",
            "created_at",
        ),

        Index(
            "ix_couple_ai_event_message",
            "message_id",
        ),

        Index(
            "ix_couple_ai_event_user_created",
            "user_id",
            "created_at",
        ),
    )
