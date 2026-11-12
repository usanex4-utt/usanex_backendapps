"""
Drops + recreates admin tables. WIPES all admins, sessions, OTPs.
Run once:  python -m backend.reset_admin_tables
"""

from sqlalchemy import inspect

from backend.database.database import engine, Base
from backend.database.admin_models import AdminUser, AdminSession, AdminOTP

print("Dropping admin tables ...")
AdminSession.__table__.drop(engine, checkfirst=True)
AdminOTP.__table__.drop(engine, checkfirst=True)
AdminUser.__table__.drop(engine, checkfirst=True)

print("Recreating with the current models ...")
Base.metadata.create_all(bind=engine, tables=[
    AdminUser.__table__, AdminSession.__table__, AdminOTP.__table__,
])

insp = inspect(engine)
for t in ("admin_users", "admin_sessions", "admin_otps"):
    cols = [c["name"] for c in insp.get_columns(t)]
    assert "email" not in cols, f"{t} still has email!"
    print(f"  {t}: {cols}")

print("✅ done")