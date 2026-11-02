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
    couple_chat,      # 👈 FIX: Couple Chat REST API import kiya
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
class CatchAllErrorMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
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
            import traceback
            print("[Usanex] Unhandled error:", repr(exc))
            traceback.print_exc()

            if response_started:
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
app.add_middleware(CatchAllErrorMiddleware)

# FIX: Render aur Vercel dono ke liye origins allow kiye
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "https://uxanexapps.vercel.app",
        "https://usanex-backendapps.onrender.com", # 👈 Render URL add kiya
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
app.include_router(couple_chat.router)      # 👈 FIX: Couple Chat API Register ki
app.include_router(profile.router)
app.include_router(reels.router)
app.include_router(search.router)
app.include_router(users.router)
app.include_router(status.router)
app.include_router(admin_moments.router)
app.include_router(moment.router)

# =========================================================
# ROOT & HEALTH ENDPOINTS
# =========================================================
@app.get("/", tags=["Root"])
def root():
    return {
        "app": "Usanex API",
        "status": "online",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }

@app.get("/health", tags=["Health"])
async def health():
    # Render par cold start se bachne ke liye ye endpoint async rakha hai
    return {"status": "ok"}