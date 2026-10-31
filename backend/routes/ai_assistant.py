# =========================================================
# USANEX — NEXA AI ASSISTANT ROUTES
# =========================================================

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database.models import User
from ..services.nexa_engine import (
    generate_reply,
    inspect_trained_checkpoint,
)
from .auth import get_current_user_from_request


router = APIRouter(
    prefix="/api/ai-assistant",
    tags=["AI Assistant"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


@router.get("/status")
def ai_assistant_status():
    checkpoint = inspect_trained_checkpoint()

    return {
        "app": "Usanex",
        "assistant": "NEXA",
        "status": "online",
        "engine": "nexa-brain-v0.16",
        "knowledge": "local-json",
        "math_engine": "enabled",
        "conversation_memory": "temporary",
        "trained_model": checkpoint,
    }


@router.post("/chat")
def nexa_chat(
    request: ChatRequest,
    user: User | None = Depends(get_current_user_from_request),
):
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Please log in to use NEXA.",
        )

    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Please enter a message.",
        )

    try:
        reply = generate_reply(
            message=message,
            user_name=user.name or "User",
            conversation_id=str(user.id),
        )

        return {
            "success": True,
            "assistant": "NEXA",
            "reply": reply,
            "engine": "nexa-brain-v0.16",
            "capabilities": {
                "math": True,
                "local_knowledge": True,
                "temporary_conversation_memory": True,
                "trained_model_generation": False,
            },
        }

    except Exception as exc:
        print(f"[NEXA] Chat error: {exc}")

        raise HTTPException(
            status_code=500,
            detail="NEXA could not process your message.",
        )
