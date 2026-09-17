"""
scripts/migrate_promote_anonymous_to_root.py
============================================
Idempotent migration: promote the user `Anonymous` from `super_admin`
(or any prior role) to the new `root_admin` role and ensure company_id IS NULL.

root_admin is the top-of-hierarchy role introduced in PR7. The Anonymous user
is the project's sole built-in root_admin.

Behavior:
  - If `Anonymous` exists and role IS NOT 'root_admin' → upgrade + clear company_id.
  - If `Anonymous` is already 'root_admin' → no-op (just normalize company_id).
  - If `Anonymous` does not exist → print warning and exit cleanly (no error).

Run: python scripts/migrate_promote_anonymous_to_root.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fix Windows terminal encoding (cp932 can't handle CJK output)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text
from sqlalchemy.engine import Engine

from auth.models import User  # noqa: F401 - registers models
from core.database import engine as default_engine


ANONYMOUS_USERNAME = "Anonymous"


def run_migration(engine: Engine) -> dict:
    """Promote Anonymous to root_admin (idempotent).

    Returns a dict of step counts:
      - exists:       1 if Anonymous exists else 0
      - role_changed: rows updated to role='root_admin'
      - company_id_cleared: rows where company_id was set to NULL
    """
    counts = {
        "exists": 0,
        "role_changed": 0,
        "company_id_cleared": 0,
    }

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, role, company_id FROM users WHERE username = :u"),
            {"u": ANONYMOUS_USERNAME},
        ).first()

        if row is None:
            print(
                f"[promote_root] WARNING: user '{ANONYMOUS_USERNAME}' does not "
                f"exist. No changes made."
            )
            return counts

        counts["exists"] = 1

        # 1) role → root_admin (if not already)
        res = conn.execute(
            text(
                "UPDATE users SET role = 'root_admin' "
                "WHERE username = :u AND role != 'root_admin'"
            ),
            {"u": ANONYMOUS_USERNAME},
        )
        counts["role_changed"] = res.rowcount or 0
        print(
            f"[promote_root] '{ANONYMOUS_USERNAME}' → root_admin: "
            f"{counts['role_changed']} row(s)"
        )

        # 2) root_admin must have company_id IS NULL
        res = conn.execute(
            text(
                "UPDATE users SET company_id = NULL "
                "WHERE username = :u AND company_id IS NOT NULL"
            ),
            {"u": ANONYMOUS_USERNAME},
        )
        counts["company_id_cleared"] = res.rowcount or 0
        print(
            f"[promote_root] '{ANONYMOUS_USERNAME}' company_id cleared: "
            f"{counts['company_id_cleared']} row(s)"
        )

    total = counts["role_changed"] + counts["company_id_cleared"]
    if total == 0:
        print("[promote_root] no changes needed.")
    else:
        print(f"[promote_root] Done. Total rows touched: {total}")

    return counts


def migrate() -> None:
    run_migration(default_engine)


if __name__ == "__main__":
    migrate()
