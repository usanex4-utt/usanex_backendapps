# =========================================================
# USANEX — FASTAPI APPLICATION ENTRY POINT
# =========================================================

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

# Import models so SQLAlchemy registers all tables
from .database.database import engine, Base
from .database import models  # noqa: F401  (registers User, Reel, ...)
from .database import couple_models  # noqa: F401  (registers couple tables)
from .database.migrate import run_migrations

from .routes import (
    auth,
    ai_assistant,
    chat,
    connections,
    couple_realtime,
    profile,
    reels,
    search,
    users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) Create any new tables
    Base.metadata.create_all(bind=engine)

    # 2) Add missing columns on existing tables (safe, non-destructive)
    try:
        run_migrations()
    except Exception as exc:  # pragma: no cover
        print(f"[Usanex] Migration warning: {exc}")

    yield


app = FastAPI(
    title="Usanex API",
    version="1.0.0",
    description=(
        "Usanex backend — Auth, Chat, Connections, Couple Chat, "
        "Reels, Search, Users and NEXA AI assistant."
    ),
    lifespan=lifespan,
)

# ----- CORS -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://uxanexapps.vercel.app"], # Hardcode karke test karein
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Router registration -----
app.include_router(auth.router)
app.include_router(ai_assistant.router)
app.include_router(chat.router)
app.include_router(connections.router)
app.include_router(couple_realtime.router)
app.include_router(profile.router)
app.include_router(reels.router)
app.include_router(search.router)
app.include_router(users.router)


# ----- Root / Health -----
@app.get("/", tags=["Root"])
def root():
    return {
        "app": "Usanex API",
        "status": "online",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
