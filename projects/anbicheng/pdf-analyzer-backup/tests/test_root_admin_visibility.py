"""
tests/test_root_admin_visibility.py
===================================
PR7: root_admin (top-of-hierarchy role) visibility & authorization tests.

Direct route-handler invocation (same pattern as test_company_admin_api.py).
Covers:
- root_admin can see other root_admin in user list
- super_admin cannot see root_admin in user list
- super_admin cannot POST a new root_admin (403)
- super_admin cannot DELETE / PATCH a root_admin (404 = hidden)
- super_admin cannot transfer a root_admin (404 = hidden)
- audit logs hide root_admin's username from super_admin viewer
- usage summary excludes root_admin from super_admin's view
- migration promotes 'Anonymous' to root_admin, idempotent (re-run = no-op)
- migration when 'Anonymous' doesn't exist → warning, no error
"""

import asyncio

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

    yield {"engine": test_engine, "SessionLocal": TestSessionLocal}

    db_module.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


# ── helpers ─────────────────────────────────────────────────────────────────

def _run(coro_or_value):
    if asyncio.iscoroutine(coro_or_value):
        return asyncio.get_event_loop().run_until_complete(coro_or_value)
    return coro_or_value


def _make_company(db, name="acme", plan="basic", max_users=5,
                  max_monthly_pages=1500, is_active=True):
    from auth.models import Company

    c = Company(
        name=name, plan=plan,
        max_users=max_users, max_monthly_pages=max_monthly_pages,
        is_active=is_active,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_user(db, username, role, company_id, is_active=True,
               display_name=None, allowed_pages=None):
    from auth.models import User

    u = User(
        username=username,
        display_name=display_name or username,
        hashed_password="x",
        role=role,
        company_id=company_id,
        is_active=is_active,
        allowed_pages=allowed_pages if allowed_pages is not None else ["jusetsu"],
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ── dependencies ────────────────────────────────────────────────────────────

def test_require_super_admin_passes_root_admin(in_memory_db):
    from auth.dependencies import require_super_admin
    from auth.models import User

    root = User(username="root", display_name="R", hashed_password="x",
                role="root_admin")
    assert require_super_admin(current_user=root) is root


def test_require_root_admin_blocks_super_admin(in_memory_db):
    from auth.dependencies import require_root_admin
    from auth.models import User

    sa = User(username="sa", display_name="S", hashed_password="x",
              role="super_admin")
    with pytest.raises(HTTPException) as exc:
        require_root_admin(current_user=sa)
    assert exc.value.status_code == 403


def test_require_root_admin_passes_root_admin(in_memory_db):
    from auth.dependencies import require_root_admin
    from auth.models import User

    root = User(username="root", display_name="R", hashed_password="x",
                role="root_admin")
    assert require_root_admin(current_user=root) is root


def test_require_company_admin_passes_root_admin(in_memory_db):
    from auth.dependencies import require_company_admin
    from auth.models import User

    root = User(username="root", display_name="R", hashed_password="x",
                role="root_admin")
    assert require_company_admin(current_user=root) is root


# ── list_users ──────────────────────────────────────────────────────────────

def test_root_admin_sees_root_admin_in_user_list(in_memory_db):
    from auth.router import list_users

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        root = _make_user(db, "root", role="root_admin", company_id=None)
        _make_user(db, "root2", role="root_admin", company_id=None)
        _make_user(db, "sa", role="super_admin", company_id=None)

        result = _run(list_users(company_id=None, admin=root, db=db))
        usernames = sorted(u.username for u in result)
        assert usernames == ["root", "root2", "sa"]
    finally:
        db.close()


def test_super_admin_cannot_see_root_admin_in_user_list(in_memory_db):
    from auth.router import list_users

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        _make_user(db, "root", role="root_admin", company_id=None)
        sa = _make_user(db, "sa", role="super_admin", company_id=None)

        result = _run(list_users(company_id=None, admin=sa, db=db))
        usernames = sorted(u.username for u in result)
        assert usernames == ["sa"]
        assert "root" not in usernames
    finally:
        db.close()


def test_company_owner_cannot_see_root_admin_in_user_list(in_memory_db):
    from auth.router import list_users

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        _make_user(db, "root", role="root_admin", company_id=None)
        _make_user(db, "m1", role="company_member", company_id=c1.id)

        result = _run(list_users(company_id=None, admin=co, db=db))
        usernames = sorted(u.username for u in result)
        assert "root" not in usernames
        assert "co" in usernames
        assert "m1" in usernames
    finally:
        db.close()


# ── create_user (POST) ──────────────────────────────────────────────────────

def test_super_admin_cannot_create_root_admin(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        body = UserCreate(
            username="newroot", display_name="NR",
            password="Abcdefg1", role="root_admin",
            company_id=None,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=sa, db=db))
        assert exc.value.status_code == 403
        assert "root" in exc.value.detail.lower() or "root" in exc.value.detail
    finally:
        db.close()


def test_company_owner_cannot_create_root_admin(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        body = UserCreate(
            username="newroot", display_name="NR",
            password="Abcdefg1", role="root_admin",
            company_id=None,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=co, db=db))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_root_admin_can_create_root_admin(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        root = _make_user(db, "root", role="root_admin", company_id=None)
        body = UserCreate(
            username="newroot", display_name="NR",
            password="Abcdefg1", role="root_admin",
            company_id=None,
        )
        u = _run(create_user(body=body, admin=root, db=db))
        assert u.role == "root_admin"
        assert u.company_id is None
    finally:
        db.close()


def test_root_admin_creating_root_admin_with_company_id_400(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        root = _make_user(db, "root", role="root_admin", company_id=None)
        c = _make_company(db, name="acme")
        body = UserCreate(
            username="newroot", display_name="NR",
            password="Abcdefg1", role="root_admin",
            company_id=c.id,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=root, db=db))
        assert exc.value.status_code == 400
    finally:
        db.close()


# ── update_user (PUT) ───────────────────────────────────────────────────────

def test_super_admin_cannot_patch_root_admin(in_memory_db):
    from auth.router import update_user
    from auth.schemas import UserUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        root = _make_user(db, "root", role="root_admin", company_id=None)

        with pytest.raises(HTTPException) as exc:
            _run(update_user(
                user_id=root.id,
                body=UserUpdate(display_name="hacked"),
                admin=sa, db=db,
            ))
        # 404 = hidden / pretending it doesn't exist
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_super_admin_cannot_promote_user_to_root_admin(in_memory_db):
    from auth.router import update_user
    from auth.schemas import UserUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        sa2 = _make_user(db, "sa2", role="super_admin", company_id=None)

        with pytest.raises(HTTPException) as exc:
            _run(update_user(
                user_id=sa2.id,
                body=UserUpdate(role="root_admin"),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 403
    finally:
        db.close()


# ── delete_user (DELETE) ────────────────────────────────────────────────────

def test_super_admin_cannot_delete_root_admin(in_memory_db):
    from auth.router import delete_user

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        root = _make_user(db, "root", role="root_admin", company_id=None)

        with pytest.raises(HTTPException) as exc:
            _run(delete_user(user_id=root.id, admin=sa, db=db))
        # 404 = hidden
        assert exc.value.status_code == 404
    finally:
        db.close()


# ── transfer_user ───────────────────────────────────────────────────────────

def test_super_admin_cannot_transfer_root_admin(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        root = _make_user(db, "root", role="root_admin", company_id=None)
        c = _make_company(db, name="dest")

        with pytest.raises(HTTPException) as exc:
            admin_transfer_user(
                user_id=root.id,
                body=UserTransferRequest(company_id=c.id),
                admin=sa, db=db,
            )
        # 404 = hidden
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_root_admin_cannot_transfer_another_root_admin(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        root = _make_user(db, "root", role="root_admin", company_id=None)
        root2 = _make_user(db, "root2", role="root_admin", company_id=None)
        c = _make_company(db, name="dest")

        with pytest.raises(HTTPException) as exc:
            admin_transfer_user(
                user_id=root2.id,
                body=UserTransferRequest(company_id=c.id),
                admin=root, db=db,
            )
        # root_admin does not belong to a Company → 400
        assert exc.value.status_code == 400
    finally:
        db.close()


# ── audit logs ──────────────────────────────────────────────────────────────

def test_super_admin_audit_logs_hide_root_admin_username(in_memory_db):
    from auth.models import AuditLog
    from auth.router import get_audit_logs

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        _make_user(db, "root", role="root_admin", company_id=None)
        db.add(AuditLog(username="root", action="login_success", ip_address="1.1.1.1"))
        db.add(AuditLog(username="sa", action="login_success", ip_address="1.1.1.2"))
        db.commit()

        logs = _run(get_audit_logs(limit=100, username=None, admin=sa, db=db))
        usernames = [l["username"] for l in logs]
        assert "root" not in usernames
        assert "sa" in usernames
    finally:
        db.close()


def test_root_admin_audit_logs_show_root_admin_username(in_memory_db):
    from auth.models import AuditLog
    from auth.router import get_audit_logs

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        root = _make_user(db, "root", role="root_admin", company_id=None)
        db.add(AuditLog(username="root", action="login_success", ip_address="1.1.1.1"))
        db.commit()

        logs = _run(get_audit_logs(limit=100, username=None, admin=root, db=db))
        usernames = [l["username"] for l in logs]
        assert "root" in usernames
    finally:
        db.close()


# ── usage summary ───────────────────────────────────────────────────────────

def test_usage_summary_excludes_root_admin_for_super_admin(in_memory_db):
    from datetime import datetime

    from auth.models import ProcessingJob
    from auth.router import get_usage_summary

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        _make_user(db, "root", role="root_admin", company_id=None)
        now = datetime.utcnow()
        db.add(ProcessingJob(
            file_id="fid1", username="root", filename="x.xlsx",
            fields_filled=1, fields_total=2, page_count=3,
            extracted_json="[]", source_type="standard", status="done",
            created_at=now,
        ))
        db.add(ProcessingJob(
            file_id="fid2", username="sa", filename="y.xlsx",
            fields_filled=2, fields_total=4, page_count=5,
            extracted_json="[]", source_type="standard", status="done",
            created_at=now,
        ))
        db.commit()

        result = _run(get_usage_summary(
            year=now.year, month=now.month, admin=sa, db=db,
        ))
        usernames = [r["username"] for r in result]
        assert "root" not in usernames
        assert "sa" in usernames
    finally:
        db.close()


def test_usage_detail_404_for_root_admin_username_via_super_admin(in_memory_db):
    from datetime import datetime

    from auth.router import get_usage_detail

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        _make_user(db, "root", role="root_admin", company_id=None)
        now = datetime.utcnow()
        with pytest.raises(HTTPException) as exc:
            _run(get_usage_detail(
                username="root", year=now.year, month=now.month,
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 404
    finally:
        db.close()


# ── migration ───────────────────────────────────────────────────────────────

def test_migration_promotes_anonymous_to_root_admin(in_memory_db):
    from auth.models import User
    from scripts.migrate_promote_anonymous_to_root import run_migration

    SessionLocal = in_memory_db["SessionLocal"]
    engine = in_memory_db["engine"]

    db = SessionLocal()
    try:
        c = _make_company(db, name="acme")
        _make_user(db, "Anonymous", role="super_admin", company_id=c.id)
    finally:
        db.close()

    counts = run_migration(engine)
    assert counts["exists"] == 1
    assert counts["role_changed"] == 1
    assert counts["company_id_cleared"] == 1

    db = SessionLocal()
    try:
        anon = db.query(User).filter(User.username == "Anonymous").one()
        assert anon.role == "root_admin"
        assert anon.company_id is None
    finally:
        db.close()


def test_migration_is_idempotent(in_memory_db):
    from scripts.migrate_promote_anonymous_to_root import run_migration

    SessionLocal = in_memory_db["SessionLocal"]
    engine = in_memory_db["engine"]

    db = SessionLocal()
    try:
        _make_user(db, "Anonymous", role="super_admin", company_id=None)
    finally:
        db.close()

    run_migration(engine)
    counts2 = run_migration(engine)
    assert counts2["exists"] == 1
    assert counts2["role_changed"] == 0
    assert counts2["company_id_cleared"] == 0


def test_migration_no_anonymous_user(in_memory_db, capsys):
    from scripts.migrate_promote_anonymous_to_root import run_migration

    engine = in_memory_db["engine"]

    counts = run_migration(engine)
    assert counts["exists"] == 0
    assert counts["role_changed"] == 0
    assert counts["company_id_cleared"] == 0

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "Anonymous" in captured.out
