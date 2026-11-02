# =========================================================
# USANEX — FASTAPI APPLICATION ENTRY POINT
# =========================================================

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings

# =========================================================
# DATABASE IMPORTS
# =========================================================

from .database.database import engine, Base
from .database import models          # noqa: F401
from .database import couple_models   # noqa: F401
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
    status,
    admin_moments,
    moment,
)


# =========================================================
# CATCH-ALL ERROR MIDDLEWARE
# =========================================================
# Catches any unhandled exception RAISED INSIDE CORS layer,
# so 500 responses still carry Access-Control-* headers.
# Without this, an endpoint crash means:
#   ServerErrorMiddleware → 500 (NO CORS headers)
#   → browser reports "blocked by CORS policy"
#   → the real error is hidden from the client
# =========================================================

class CatchAllErrorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Pass-through for websockets / lifespan / etc.
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        response_started = False

        async def send_tracker(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracker)
        except Exception as exc:
            # Always log the real traceback for developers
            import traceback
            print("[Usanex] Unhandled error:", repr(exc))
            traceback.print_exc()

            if response_started:
                # Streaming response already began — can't send another one
                raise

            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )
            await response(scope, receive, send)


# =========================================================
# APPLICATION LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("[Usanex] Starting application...")

    # 1. DATABASE SETUP
    Base.metadata.create_all(bind=engine)

    # 2. DATABASE MIGRATIONS
    run_migrations()
    print("[Usanex] Database migrations completed")

    # 3. REDIS INITIALIZATION
    init_redis()

    # 4. REDIS HEALTH CHECK
    if not await ping_redis():
        raise RuntimeError("Redis is not reachable!")

    print("[Usanex] Redis Connected successfully")

    # 5. START WEBSOCKET REDIS MANAGER
    await redis_ws_manager.start()
    print("[Usanex] WebSocket Redis Manager started")

    try:
        yield
    finally:
        # 6. STOP WEBSOCKET REDIS MANAGER
        print("[Usanex] Stopping WebSocket Redis Manager...")
        await redis_ws_manager.stop()

        # 7. CLOSE REDIS CONNECTION
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
# MIDDLEWARE — ORDER MATTERS
# =========================================================
# Starlette builds the chain in REVERSE order of add_middleware.
# The LAST added is the OUTERMOST.
#
# Target request path:
#   ServerError → CORS → CatchAll → Exception → Router
#
# So: add CatchAll FIRST, then CORS LAST.
# Result: CORS's wrapped `send` is still active when CatchAll
# produces its 500 → headers get added → client sees the error.
# =========================================================

app.add_middleware(CatchAllErrorMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "https://uxanexapps.vercel.app",
    ],
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
app.include_router(status.router)
app.include_router(admin_moments.router)
app.include_router(moment.router)


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
def health():
    return {"status": "ok"}
