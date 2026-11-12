# backend/routes/admin_dashboard.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import os

from backend.database.database import get_db
from backend.database.models import User, Post, Reel, ReelReport
from backend.routes.admin_auth import get_current_admin
from backend.core.redis_client import ping_redis

router = APIRouter(
    prefix="/api/admin/dashboard",
    tags=["Admin Dashboard"],
    dependencies=[Depends(get_current_admin)] 
)

# ==========================================
# 1. OVERVIEW CARDS
# ==========================================
@router.get("/overview")
def get_overview(db: Session = Depends(get_db)):
    # 1. Total Users
    total_users = db.query(User).count()
    
    # 2. Active Users
    active_users = db.query(User).filter(User.is_active == True).count()
    
    # 3. New Registrations (Last 7 Days)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    new_registrations = db.query(User).filter(User.created_at >= seven_days_ago).count()
    
    # 4. Pending Reports (Safe Query)
    reel_reports = 0
    moment_reports = 0
    try:
        reel_reports = db.query(ReelReport).count()
    except Exception:
        pass
    
    try:
        moment_reports = db.execute(text("SELECT COUNT(*) FROM nex_moment_reports")).scalar() or 0
    except Exception:
        pass
        
    pending_reports = reel_reports + moment_reports

    return {
        "total_users": total_users,
        "active_users": active_users,
        "new_registrations": new_registrations,
        "pending_reports": pending_reports
    }

@router.get("/users")
def get_users_graph(db: Session = Depends(get_db)):
    # आज की तारीख (UTC) से पिछले 7 दिन निकालें
    today = datetime.utcnow().date()
    seven_days_ago = today - timedelta(days=6) # आज + पिछले 6 दिन = 7 दिन

    # डेटाबेस से ग्रुप करके डेटा निकालें
    results = (
        db.query(
            func.date(User.created_at).label("date"),
            func.count(User.id).label("users")
        )
        .filter(User.created_at >= seven_days_ago)
        .group_by(func.date(User.created_at))
        .all()
    )

    # डेटाबेस के रिजल्ट को डिक्शनरी में बदलें { "2024-05-01": 5, ... }
    counts = {}
    for row in results:
        # row.date कभी date object होता है, कभी string — दोनों को स्ट्रिंग में बदलें
        date_str = str(row.date) 
        counts[date_str] = row.users

    # अब पिछले 7 दिनों की पूरी लिस्ट बनाएँ (अगर किसी दिन 0 है तो 0 डालें)
    graph_data = []
    for i in range(7):
        day = seven_days_ago + timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        graph_data.append({
            "date": day_str,
            "users": counts.get(day_str, 0)  # अगर उस दिन कोई नहीं है तो 0
        })

    return graph_data

   

# ==========================================
# 3. CONTENT ACTIVITY
# ==========================================
@router.get("/content")
def get_content_activity(db: Session = Depends(get_db)):
    total_posts = 0
    total_reels = 0
    total_nex_moments = 0

    try:
        total_posts = db.query(Post).count()
    except Exception as e:
        print(f"Error fetching posts: {e}")

    try:
        total_reels = db.query(Reel).count()
    except Exception as e:
        print(f"Error fetching reels: {e}")

    try:
        # Nex Moments Count
        result = db.execute(text("SELECT COUNT(*) FROM nex_moments WHERE deleted_at IS NULL")).scalar()
        total_nex_moments = result if result else 0
    except Exception:
        # Fallback if deleted_at column is missing
        try:
            result = db.execute(text("SELECT COUNT(*) FROM nex_moments")).scalar()
            total_nex_moments = result if result else 0
        except Exception as e2:
            print(f"Error fetching nex moments: {e2}")

    return {
        "total_posts": total_posts,
        "total_reels": total_reels,
        "total_nex_moments": total_nex_moments
    }

# ==========================================
# 4. SERVER HEALTH
# ==========================================
@router.get("/health")
async def get_server_health(db: Session = Depends(get_db)):
    db_status = "Offline"
    try:
        db.execute(text("SELECT 1"))
        db_status = "Online"
    except Exception:
        pass
        
    redis_ok = await ping_redis()
    
    supabase_status = "Disconnected"
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        supabase_status = "Connected"
    
    overall_status = "Online" if db_status == "Online" and redis_ok else "Degraded"
    
    return {
        "status": overall_status,
        "database": db_status,
        "redis": "Connected" if redis_ok else "Disconnected",
        "supabase": supabase_status
    }

# ==========================================
# 5. RECENT ACTIVITY
# ==========================================
@router.get("/activity")
def get_recent_activity(db: Session = Depends(get_db)):
    activities = []
    
    # 1. Recent Users
    recent_users = db.query(User).order_by(User.created_at.desc()).limit(5).all()
    for u in recent_users:
        activities.append({
            "message": f"New User Registered: {u.name} (@{u.username})",
            "time": u.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        })
        
    # 2. Recent Moments
    try:
        recent_moments = db.execute(text("""
            SELECT user_id, moment_type, created_at 
            FROM nex_moments 
            WHERE deleted_at IS NULL 
            ORDER BY created_at DESC LIMIT 5
        """)).mappings().all()
        
        for m in recent_moments:
            activities.append({
                "message": f"New {m['moment_type'].capitalize()} Moment created by {m['user_id']}",
                "time": m['created_at'].strftime("%Y-%m-%d %H:%M:%S"),
            })
    except Exception as e:
        print(f"Error fetching recent moments: {e}")
        
    # Sort by string "time" to avoid timezone errors
    activities.sort(key=lambda x: x["time"], reverse=True)
    
    # Return top 5
    return activities[:5]