
# =========================================================
# USANEX — FASTAPI APPLICATION ENTRY POINT
# =========================================================

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

# =========================================================
# DATABASE IMPORTS
# =========================================================

from .database.database import engine, Base
from .database import models  # noqa: F401
from .database import couple_models  # noqa: F401
from .database.migrate import run_migrations

# =========================================================
# REDIS IMPORTS
# =========================================================

from .core.redis_client import (
    init_redis,
    close_redis,
    ping_redis,
)

# =========================================================
# WEBSOCKET REDIS MANAGER
# =========================================================

from .websocket.redis_manager import redis_ws_manager

# =========================================================
# ROUTER IMPORTS
# =========================================================

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


# =========================================================
# APPLICATION LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("[Usanex] Starting application...")

    # -----------------------------------------------------
    # 1. DATABASE SETUP
    # -----------------------------------------------------

    Base.metadata.create_all(bind=engine)

    # -----------------------------------------------------
    # 2. DATABASE MIGRATIONS
    # -----------------------------------------------------

    run_migrations()

    print("[Usanex] Database migrations completed")

    # -----------------------------------------------------
    # 3. REDIS INITIALIZATION
    # -----------------------------------------------------

    init_redis()

    # -----------------------------------------------------
    # 4. REDIS HEALTH CHECK
    # -----------------------------------------------------

    if not await ping_redis():
        raise RuntimeError("Redis is not reachable!")

    print("[Usanex] Redis Connected successfully")

    # -----------------------------------------------------
    # 5. START WEBSOCKET REDIS MANAGER
    # -----------------------------------------------------

    await redis_ws_manager.start()

    print("[Usanex] WebSocket Redis Manager started")

    # -----------------------------------------------------
    # APPLICATION RUNNING
    # -----------------------------------------------------

    try:
        yield

    finally:

        # -------------------------------------------------
        # 6. STOP WEBSOCKET REDIS MANAGER
        # -------------------------------------------------

        print("[Usanex] Stopping WebSocket Redis Manager...")

        await redis_ws_manager.stop()

        # -------------------------------------------------
        # 7. CLOSE REDIS CONNECTION
        # -------------------------------------------------

        await close_redis()

        print("[Usanex] Redis Disconnected")

        print("[Usanex] Shutdown complete")


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="Usanex API",
    version="1.0.0",
    description=(
        "Usanex backend — Auth, Chat, Connections, Couple Chat, "
        "Reels, Search, Users and NEXA AI assistant."
    ),
    lifespan=lifespan,
)


# =========================================================
# CORS CONFIGURATION
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# ROUTER REGISTRATION
# =========================================================

app.include_router(auth.router)
app.include_router(ai_assistant.router)
app.include_router(chat.router)
app.include_router(connections.router)
app.include_router(couple_realtime.router)
app.include_router(profile.router)
app.include_router(reels.router)
app.include_router(search.router)
app.include_router(users.router)


# =========================================================
# ROOT ENDPOINT
# =========================================================

@app.get("/", tags=["Root"])
def root():

    return {
        "app": "Usanex API",
        "status": "online",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health", tags=["Health"])
async def health():

    redis_status = await ping_redis()

    return {
        "status": "ok",
        "database": "configured",
        "redis": (
            "connected"
            if redis_status
            else "disconnected"
        ),
    }
