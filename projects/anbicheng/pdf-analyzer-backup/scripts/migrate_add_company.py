"""
scripts/migrate_add_company.py
==============================
Idempotent migration:
- Creates companies table (if missing).
- Adds users.company_id and processing_jobs.company_id columns (if missing).
- Inserts a 'default' Company and back-fills existing rows.

Run: python scripts/migrate_add_company.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fix Windows terminal encoding (cp932 can't handle CJK output)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text

from auth.models import Company, ProcessingJob, User  # noqa: F401 - registers models
from core.database import Base, SessionLocal, engine


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False
    return column in [c["name"] for c in inspector.get_columns(table)]


def _add_company_id_column(table: str) -> bool:
    """Adds <table>.company_id (INTEGER, FK->companies.id) if missing. Returns True if added."""
    if _has_column(table, "company_id"):
        print(f"[migrate] {table}.company_id already exists. Skipping ALTER.")
        return False
    sql = f"ALTER TABLE {table} ADD COLUMN company_id INTEGER REFERENCES companies(id)"
    with engine.begin() as conn:
        conn.execute(text(sql))
    print(f"[migrate] Added {table}.company_id column.")
    return True


def migrate() -> None:
    inspector = inspect(engine)

    # 1) Create missing tables (companies, and any other model not yet materialized).
    #    create_all only creates tables that don't exist; existing tables are untouched.
    if "companies" not in inspector.get_table_names():
        print("[migrate] 'companies' table missing — creating via metadata.create_all ...")
        Base.metadata.create_all(bind=engine)
        print("[migrate] Tables created.")
    else:
        print("[migrate] 'companies' table already exists.")

    # 2) Add company_id columns where missing.
    _add_company_id_column("users")
    _add_company_id_column("processing_jobs")

    # 3) Insert default Company if missing.
    db = SessionLocal()
    try:
        default_company = db.query(Company).filter(Company.name == "default").one_or_none()
        if default_company is None:
            default_company = Company(
                name="default",
                plan="internal",
                max_users=9999,
                max_monthly_pages=99999999,
                is_active=True,
            )
            db.add(default_company)
            db.commit()
            db.refresh(default_company)
            print(f"[migrate] Inserted default Company (id={default_company.id}).")
        else:
            print(f"[migrate] Default Company already exists (id={default_company.id}).")

        # 4) Back-fill users.company_id where NULL.
        #    Skip super_admin (PR2 keeps super_admin.company_id = NULL by design).
        users_updated = (
            db.query(User)
            .filter(User.company_id.is_(None), User.role != "super_admin")
            .update({User.company_id: default_company.id}, synchronize_session=False)
        )
        db.commit()
        print(f"[migrate] Updated {users_updated} users.")

        # 5) Back-fill processing_jobs.company_id where NULL.
        jobs_updated = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.company_id.is_(None))
            .update({ProcessingJob.company_id: default_company.id}, synchronize_session=False)
        )
        db.commit()
        print(f"[migrate] Updated {jobs_updated} processing_jobs.")
    finally:
        db.close()

    print("[migrate] Done.")


if __name__ == "__main__":
    migrate()
