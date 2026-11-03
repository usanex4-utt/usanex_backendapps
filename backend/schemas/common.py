# backend/schemas/common.py
"""Shared base classes for all schemas."""

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """
    Base for every schema.
    - from_attributes=True → direct ORM (SQLAlchemy) → Pydantic conversion
    - extra="ignore"        → unknown keys silently dropped
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class MessageResponse(BaseSchema):
    """Generic `{"message": "..."}` response."""
    message: str