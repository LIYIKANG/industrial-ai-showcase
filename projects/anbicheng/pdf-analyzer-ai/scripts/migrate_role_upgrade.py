"""
scripts/migrate_role_upgrade.py
===============================
Idempotent migration: upgrade legacy role values to the multi-tenancy role model.

Mapping:
  owner    → super_admin
  admin    → super_admin
  operator → company_member

Also clears company_id for super_admin rows (super_admin must not belong to a Company).

Re-running this script after all rows have been upgraded is a no-op (prints
"no changes needed").

Run: python scripts/migrate_role_upgrade.py
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


def run_migration(engine: Engine) -> dict:
    """Execute the role-upgrade migration against the given engine.

    Returns a dict of step-by-step counts. Idempotent: re-running yields zeros.
    """
    counts = {
        "to_super_admin": 0,
        "to_company_member": 0,
        "company_id_cleared": 0,
    }

    with engine.begin() as conn:
        # 1) owner / admin → super_admin
        res = conn.execute(
            text(
                "UPDATE users SET role = 'super_admin' "
                "WHERE role IN ('owner', 'admin')"
            )
        )
        counts["to_super_admin"] = res.rowcount or 0
        print(
            f"[migrate_role] owner/admin → super_admin: "
            f"{counts['to_super_admin']} row(s)"
        )

        # 2) operator → company_member
        res = conn.execute(
            text(
                "UPDATE users SET role = 'company_member' "
                "WHERE role = 'operator'"
            )
        )
        counts["to_company_member"] = res.rowcount or 0
        print(
            f"[migrate_role] operator → company_member: "
            f"{counts['to_company_member']} row(s)"
        )

        # 3) super_admin must not belong to a Company
        res = conn.execute(
            text(
                "UPDATE users SET company_id = NULL "
                "WHERE role = 'super_admin' AND company_id IS NOT NULL"
            )
        )
        counts["company_id_cleared"] = res.rowcount or 0
        print(
            f"[migrate_role] super_admin company_id cleared: "
            f"{counts['company_id_cleared']} row(s)"
        )

    total = sum(counts.values())
    if total == 0:
        print("[migrate_role] no changes needed.")
    else:
        print(f"[migrate_role] Done. Total rows touched: {total}")

    return counts


def migrate() -> None:
    run_migration(default_engine)


if __name__ == "__main__":
    migrate()
