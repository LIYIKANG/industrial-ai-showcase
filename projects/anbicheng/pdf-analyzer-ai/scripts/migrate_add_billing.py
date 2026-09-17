"""
scripts/migrate_add_billing.py
==============================
Idempotent migration for Stripe billing (Phase 1):
- Adds companies.stripe_customer_id, stripe_subscription_id,
  subscription_status, current_period_end columns (if missing).

Run: python scripts/migrate_add_billing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fix Windows terminal encoding (cp932 can't handle CJK output)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text

from auth.models import Company  # noqa: F401 - registers model
from core.database import engine

# column name -> SQL type / DDL fragment (SQLite & PostgreSQL compatible)
_COLUMNS = {
    "stripe_customer_id": "VARCHAR(64)",
    "stripe_subscription_id": "VARCHAR(64)",
    "subscription_status": "VARCHAR(20) DEFAULT 'inactive' NOT NULL",
    "current_period_end": "TIMESTAMP",
}


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False
    return column in [c["name"] for c in inspector.get_columns(table)]


def migrate() -> None:
    if "companies" not in inspect(engine).get_table_names():
        print("[migrate] 'companies' table does not exist. Run scripts/init_db.py first.")
        sys.exit(1)

    for column, ddl in _COLUMNS.items():
        if _has_column("companies", column):
            print(f"[migrate] companies.{column} already exists. Skipping.")
            continue
        sql = f"ALTER TABLE companies ADD COLUMN {column} {ddl}"
        with engine.begin() as conn:
            conn.execute(text(sql))
        print(f"[migrate] Added companies.{column}.")

    print("[migrate] Done.")


if __name__ == "__main__":
    migrate()
