"""
scripts/migrate_add_allowed_pages.py
====================================
既存の users テーブルに allowed_pages カラムを追加するマイグレーション。

Usage:
  python scripts/migrate_add_allowed_pages.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from auth.models import ALL_PAGES
from core.database import engine


def migrate() -> None:
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("users")]

    if "allowed_pages" in columns:
        print("[migrate] 'allowed_pages' column already exists. Skipping.")
        return

    default_pages = json.dumps(list(ALL_PAGES))
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE users ADD COLUMN allowed_pages TEXT NOT NULL DEFAULT '{default_pages}'")
        )
    print(f"[migrate] Added 'allowed_pages' column with default {default_pages}.")
    print("[migrate] Done.")


if __name__ == "__main__":
    migrate()
