"""
tests/test_role_migration.py
============================
Tests for PR2 role-upgrade migration and the new role-based dependencies.

- Migration: owner/admin → super_admin, operator → company_member, and
  super_admin.company_id is cleared. Re-running is a no-op.
- Dependencies: require_super_admin and require_company_admin enforce the
  expected role gate.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def in_memory_db(monkeypatch):
    """In-memory SQLite DB shared across sessions via StaticPool."""
    import core.database as db_module

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    from auth import models  # noqa: F401

    db_module.Base.metadata.create_all(bind=test_engine)

    yield {
        "engine": test_engine,
        "SessionLocal": TestSessionLocal,
        "db_module": db_module,
    }

    db_module.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def _seed_legacy_users(SessionLocal, default_company_id):
    """Insert one user per legacy role + a row already on the new role."""
    from auth.models import User

    db = SessionLocal()
    try:
        db.add(User(
            username="legacy_owner",
            display_name="Owner",
            hashed_password="x",
            role="owner",
            company_id=default_company_id,
            allowed_pages=["jusetsu"],
        ))
        db.add(User(
            username="legacy_admin",
            display_name="Admin",
            hashed_password="x",
            role="admin",
            company_id=default_company_id,
            allowed_pages=["jusetsu"],
        ))
        db.add(User(
            username="legacy_op",
            display_name="Operator",
            hashed_password="x",
            role="operator",
            company_id=default_company_id,
            allowed_pages=["jusetsu"],
        ))
        db.commit()
    finally:
        db.close()


def _seed_default_company(SessionLocal) -> int:
    from auth.models import Company

    db = SessionLocal()
    try:
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
        return company.id
    finally:
        db.close()


def test_role_migration_upgrades_all_roles(in_memory_db):
    from auth.models import User
    from scripts.migrate_role_upgrade import run_migration

    SessionLocal = in_memory_db["SessionLocal"]
    engine = in_memory_db["engine"]

    company_id = _seed_default_company(SessionLocal)
    _seed_legacy_users(SessionLocal, company_id)

    counts = run_migration(engine)
    assert counts["to_super_admin"] == 2  # owner + admin
    assert counts["to_company_member"] == 1  # operator
    assert counts["company_id_cleared"] == 2  # both former owner/admin had company_id

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.username == "legacy_owner").one()
        admin = db.query(User).filter(User.username == "legacy_admin").one()
        op = db.query(User).filter(User.username == "legacy_op").one()

        assert owner.role == "super_admin"
        assert owner.company_id is None
        assert admin.role == "super_admin"
        assert admin.company_id is None
        assert op.role == "company_member"
        # operator's company_id MUST be preserved
        assert op.company_id == company_id
    finally:
        db.close()


def test_role_migration_is_idempotent(in_memory_db):
    from auth.models import User
    from scripts.migrate_role_upgrade import run_migration

    SessionLocal = in_memory_db["SessionLocal"]
    engine = in_memory_db["engine"]

    company_id = _seed_default_company(SessionLocal)
    _seed_legacy_users(SessionLocal, company_id)

    run_migration(engine)
    counts2 = run_migration(engine)

    assert counts2["to_super_admin"] == 0
    assert counts2["to_company_member"] == 0
    assert counts2["company_id_cleared"] == 0

    db = SessionLocal()
    try:
        roles = sorted(u.role for u in db.query(User).all())
        assert roles == ["company_member", "super_admin", "super_admin"]
    finally:
        db.close()


def test_require_super_admin_blocks_company_member(in_memory_db):
    from auth.dependencies import require_super_admin
    from auth.models import User

    member = User(
        username="m",
        display_name="M",
        hashed_password="x",
        role="company_member",
    )
    with pytest.raises(HTTPException) as exc:
        require_super_admin(current_user=member)
    assert exc.value.status_code == 403


def test_require_super_admin_passes_super_admin(in_memory_db):
    from auth.dependencies import require_super_admin
    from auth.models import User

    sa = User(
        username="s",
        display_name="S",
        hashed_password="x",
        role="super_admin",
    )
    assert require_super_admin(current_user=sa) is sa


def test_require_super_admin_blocks_company_owner(in_memory_db):
    from auth.dependencies import require_super_admin
    from auth.models import User

    co = User(
        username="co",
        display_name="CO",
        hashed_password="x",
        role="company_owner",
    )
    with pytest.raises(HTTPException) as exc:
        require_super_admin(current_user=co)
    assert exc.value.status_code == 403


def test_require_company_admin_blocks_company_member(in_memory_db):
    from auth.dependencies import require_company_admin
    from auth.models import User

    member = User(
        username="m",
        display_name="M",
        hashed_password="x",
        role="company_member",
    )
    with pytest.raises(HTTPException) as exc:
        require_company_admin(current_user=member)
    assert exc.value.status_code == 403


def test_require_company_admin_passes_super_admin_and_company_owner(in_memory_db):
    from auth.dependencies import require_company_admin
    from auth.models import User

    sa = User(
        username="s",
        display_name="S",
        hashed_password="x",
        role="super_admin",
    )
    co = User(
        username="co",
        display_name="CO",
        hashed_password="x",
        role="company_owner",
    )
    assert require_company_admin(current_user=sa) is sa
    assert require_company_admin(current_user=co) is co
