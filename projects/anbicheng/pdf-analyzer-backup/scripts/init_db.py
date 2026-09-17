"""
scripts/init_db.py
==================
Database initialization: create tables + initial admin account.

Usage:
  python scripts/init_db.py
  APP_ENV=production python scripts/init_db.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fix Windows terminal encoding (cp932 can't handle CJK paths)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import auth.models  # noqa: F401 - triggers model registration
from auth.models import Company
from auth.service import create_user, get_user, update_user
from core.config import settings
from core.database import Base, SessionLocal, engine


def _ensure_default_company(db) -> Company:
    """Idempotently ensure the 'default' Company exists and return it."""
    company = db.query(Company).filter(Company.name == "default").one_or_none()
    if company:
        print(f"[init_db] Default Company already exists (id={company.id}).")
        return company
    company = Company(
        name="default",
        plan="internal",
        max_users=9999,
        max_monthly_pages=99999999,
        is_active=True,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    print(f"[init_db] Default Company created (id={company.id}).")
    return company


def init() -> None:
    print(f"[init_db] env={settings.APP_ENV}  db={settings.DATABASE_URL}")

    print("[init_db] Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("[init_db] Tables created.")

    username = os.getenv("INIT_ADMIN_USERNAME", "admin")
    password = os.getenv("INIT_ADMIN_PASSWORD", "")
    display_name = os.getenv("INIT_ADMIN_DISPLAY_NAME", "Admin")

    if not password:
        env_file = ".env" if settings.is_development else ".env.production"
        print(f"[init_db] ERROR: INIT_ADMIN_PASSWORD is not set in {env_file}")
        sys.exit(1)

    db = SessionLocal()
    try:
        # Ensure default Company exists (for company_member users); super_admin
        # itself does NOT belong to any Company.
        _ensure_default_company(db)

        existing = get_user(db, username)
        if existing:
            print(f"[init_db] Admin '{username}' already exists. Skipping.")
        else:
            admin = create_user(
                db,
                username=username,
                display_name=display_name,
                password=password,
                role="super_admin",
                must_change_password=False,
                company_id=None,
            )
            print(f"[init_db] '{admin.username}' created as super_admin (company_id=None).")
    finally:
        db.close()

    print("[init_db] Done.")


if __name__ == "__main__":
    init()
