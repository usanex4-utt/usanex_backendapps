# backend/services/nexa_engine.py
import json
import re
from pathlib import Path
from typing import Optional

_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
_DATA_DIR = _ML_DIR / "data"

_MODEL_FILE = _DATA_DIR / "nexa_model.json"
_TRAIN_FILE = _DATA_DIR / "train.jsonl"
_CHECKPOINT_FILE = _DATA_DIR / "nexa_brain_v0_16_trained.pt"

_conversations: dict[str, list[dict]] = {}
_MAX_HISTORY = 20

# 👇 NEW — one-time caches
_MODEL_CACHE: Optional[dict] = None
_EXAMPLES_CACHE: Optional[list[dict]] = None


def _load_model() -> Optional[dict]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if not _MODEL_FILE.exists():
        return None
    try:
        _MODEL_CACHE = json.loads(_MODEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        _MODEL_CACHE = None
    return _MODEL_CACHE


def _load_examples() -> list[dict]:
    global _EXAMPLES_CACHE
    if _EXAMPLES_CACHE is not None:
        return _EXAMPLES_CACHE
    if not _TRAIN_FILE.exists():
        _EXAMPLES_CACHE = []
        return _EXAMPLES_CACHE
    examples: list[dict] = []
    with _TRAIN_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("question") and item.get("answer"):
                examples.append(item)
    _EXAMPLES_CACHE = examples
    return _EXAMPLES_CACHE


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9@']+", text.lower()))


def _score(question_tokens: set[str], example: dict) -> float:
    if not question_tokens:
        return 0.0
    example_tokens = _tokens(example.get("question", ""))
    if not example_tokens:
        return 0.0
    overlap = len(question_tokens & example_tokens)
    union = len(question_tokens | example_tokens)
    precision = overlap / len(question_tokens)
    recall = overlap / len(example_tokens)
    jaccard = overlap / union if union else 0.0
    return max(jaccard, 0.6 * precision + 0.4 * recall)


def _retrieve(message: str) -> Optional[str]:
    examples = _load_examples()
    if not examples:
        return None
    qtokens = _tokens(message)
    best: Optional[dict] = None
    best_score = 0.0
    for ex in examples:
        s = _score(qtokens, ex)
        if s > best_score:
            best_score = s
            best = ex
    if best_score < 0.25 or best is None:
        return None
    return best.get("answer")


_ALLOWED_MATH = re.compile(r"^[0-9+\-*/().^%\s]+$")


def _math(message: str) -> Optional[str]:
    expr = message.strip()
    if not expr or not _ALLOWED_MATH.match(expr):
        return None
    if not any(ch.isdigit() for ch in expr):
        return None
    try:
        result = eval(expr.replace("^", "**"), {"__builtins__": {}}, {})
        return f"Answer: {result}"
    except Exception:
        return None


_GREETING = re.compile(
    r"\b(hi|hello|hey|namaste|namaskar|salam)\b", re.IGNORECASE
)


def generate_reply(
    message: str,
    user_name: str = "User",
    conversation_id: Optional[str] = None,
) -> str:

    if not message or not message.strip():
        return "Please type something."

    key = conversation_id or user_name or "default"
    history = _conversations.setdefault(key, [])
    history.append({"role": "user", "content": message})
    history[:] = history[-_MAX_HISTORY:]

    math_reply = _math(message)
    if math_reply:
        history.append({"role": "assistant", "content": math_reply})
        return math_reply

    if _GREETING.search(message):
        reply = f"Namaste {user_name}! Main NEXA hoon. Kaise madad karun?"
        history.append({"role": "assistant", "content": reply})
        return reply

    retrieved = _retrieve(message)
    if retrieved:
        history.append({"role": "assistant", "content": retrieved})
        return retrieved

    fallback = (
        "Is sawal ka jawab mere current knowledge mein nahi hai. "
        "Kripya apna sawal dobara ya alag tarike se poochhein."
    )
    history.append({"role": "assistant", "content": fallback})
    return fallback


def inspect_trained_checkpoint() -> dict:
    if not _CHECKPOINT_FILE.exists():
        return {"available": False, "path": None}
    try:
        return {
            "available": True,
            "path": str(_CHECKPOINT_FILE),
            "size_bytes": _CHECKPOINT_FILE.stat().st_size,
            "model_file": str(_MODEL_FILE) if _MODEL_FILE.exists() else None,
        }
    except Exception:
        return {"available": False, "path": None}