"""
tests/test_company_admin_api.py
===============================
Tests for PR4 admin endpoints:
- Company CRUD (super_admin only)
- User-creation hardening (company_id / role rules)
- User list/update/delete scoping (super_admin vs company_owner)

We invoke route handlers directly (in line with test_role_migration.py / test_quota.py)
rather than going through FastAPI's TestClient. This keeps tests fast and avoids
spinning up the full app (which would require CLAUDE_API_KEY etc).
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
    """Await coroutines, return values as-is (some handlers are async, some sync)."""
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


# ── Company CRUD (super_admin) ──────────────────────────────────────────────

def test_super_admin_can_create_list_update_company(in_memory_db):
    from auth.router import (
        admin_create_company,
        admin_list_companies,
        admin_update_company,
    )
    from auth.schemas import CompanyCreate, CompanyUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)

        body = CompanyCreate(name="Acme", plan="basic")
        c = _run(admin_create_company(body=body, admin=sa, db=db))
        assert c.id is not None
        assert c.name == "Acme"
        assert c.plan == "basic"
        # plan defaults applied
        assert c.max_users == 2
        assert c.max_monthly_pages == 50 * 30

        listing = _run(admin_list_companies(admin=sa, db=db))
        assert any(item["name"] == "Acme" for item in listing)
        acme_entry = next(item for item in listing if item["name"] == "Acme")
        assert acme_entry["active_user_count"] == 0
        assert acme_entry["monthly_pages_used"] == 0

        updated = _run(admin_update_company(
            company_id=c.id,
            body=CompanyUpdate(name="Acme2"),
            admin=sa,
            db=db,
        ))
        assert updated.name == "Acme2"
    finally:
        db.close()


def test_company_owner_cannot_access_companies_endpoints(in_memory_db):
    from auth.dependencies import require_super_admin
    from auth.models import User

    co = User(
        username="co", display_name="CO", hashed_password="x",
        role="company_owner", company_id=1,
    )
    # Route handlers depend on require_super_admin → invoking that gate raises 403
    with pytest.raises(HTTPException) as exc:
        require_super_admin(current_user=co)
    assert exc.value.status_code == 403


def test_duplicate_company_name_returns_409(in_memory_db):
    from auth.router import admin_create_company
    from auth.schemas import CompanyCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        _make_company(db, name="dup")
        with pytest.raises(HTTPException) as exc:
            _run(admin_create_company(
                body=CompanyCreate(name="dup", plan="basic"),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 409
    finally:
        db.close()


def test_patch_plan_switch_applies_defaults(in_memory_db):
    from auth.router import admin_update_company
    from auth.schemas import CompanyUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        c = _make_company(db, name="x", plan="basic", max_users=2, max_monthly_pages=1500)

        # Switch to "pro" — limits omitted → defaults applied
        updated = _run(admin_update_company(
            company_id=c.id,
            body=CompanyUpdate(plan="pro"),
            admin=sa, db=db,
        ))
        assert updated.plan == "pro"
        assert updated.max_users == 4
        assert updated.max_monthly_pages == 200 * 30
    finally:
        db.close()


def test_patch_unknown_plan_returns_400(in_memory_db):
    from auth.router import admin_update_company
    from auth.schemas import CompanyUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        c = _make_company(db, name="x")
        with pytest.raises(HTTPException) as exc:
            _run(admin_update_company(
                company_id=c.id,
                body=CompanyUpdate(plan="nonsense"),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 400
    finally:
        db.close()


# ── User creation (POST /api/admin/users) hardening ─────────────────────────

def test_super_admin_creating_company_member_without_company_id_400(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        body = UserCreate(
            username="newm", display_name="NM",
            password="Abcdefg1", role="company_member",
            company_id=None,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=sa, db=db))
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_super_admin_creating_super_admin_with_company_id_400(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        c = _make_company(db, name="acme")
        body = UserCreate(
            username="newsa", display_name="NSA",
            password="Abcdefg1", role="super_admin",
            company_id=c.id,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=sa, db=db))
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_super_admin_creating_super_admin_without_company_id_ok(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        body = UserCreate(
            username="newsa", display_name="NSA",
            password="Abcdefg1", role="super_admin",
            company_id=None,
        )
        u = _run(create_user(body=body, admin=sa, db=db))
        assert u.role == "super_admin"
        assert u.company_id is None
    finally:
        db.close()


def test_company_owner_creating_company_owner_403(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c = _make_company(db, name="acme")
        co = _make_user(db, "co", role="company_owner", company_id=c.id)
        body = UserCreate(
            username="newco", display_name="NCO",
            password="Abcdefg1", role="company_owner",
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=co, db=db))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_company_owner_creating_company_member_success_auto_company_id(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c = _make_company(db, name="acme", max_users=10)
        co = _make_user(db, "co", role="company_owner", company_id=c.id)

        # Even if owner *passes* a different company_id, it is ignored.
        body = UserCreate(
            username="newmem", display_name="NM",
            password="Abcdefg1", role="company_member",
            company_id=9999,  # should be ignored
        )
        u = _run(create_user(body=body, admin=co, db=db))
        assert u.role == "company_member"
        assert u.company_id == c.id
    finally:
        db.close()


def test_super_admin_creating_company_member_nonexistent_company_id_404(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        body = UserCreate(
            username="newmem", display_name="NM",
            password="Abcdefg1", role="company_member",
            company_id=9999,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=sa, db=db))
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_super_admin_creating_user_for_inactive_company_403(in_memory_db):
    from auth.router import create_user
    from auth.schemas import UserCreate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        c = _make_company(db, name="dead", is_active=False)
        body = UserCreate(
            username="newmem", display_name="NM",
            password="Abcdefg1", role="company_member",
            company_id=c.id,
        )
        with pytest.raises(HTTPException) as exc:
            _run(create_user(body=body, admin=sa, db=db))
        assert exc.value.status_code == 403
    finally:
        db.close()


# ── User list scoping ───────────────────────────────────────────────────────

def test_company_owner_lists_only_their_company_users(in_memory_db):
    from auth.router import list_users

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        c2 = _make_company(db, name="c2")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        _make_user(db, "m1", role="company_member", company_id=c1.id)
        _make_user(db, "m2", role="company_member", company_id=c2.id)
        _make_user(db, "sa", role="super_admin", company_id=None)

        result = _run(list_users(company_id=None, admin=co, db=db))
        usernames = sorted(u.username for u in result)
        assert usernames == ["co", "m1"]
    finally:
        db.close()


def test_super_admin_lists_all_or_filters_by_company_id(in_memory_db):
    from auth.router import list_users

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        c2 = _make_company(db, name="c2")
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        _make_user(db, "m1", role="company_member", company_id=c1.id)
        _make_user(db, "m2", role="company_member", company_id=c2.id)

        all_ = _run(list_users(company_id=None, admin=sa, db=db))
        assert sorted(u.username for u in all_) == ["m1", "m2", "sa"]

        filtered = _run(list_users(company_id=c1.id, admin=sa, db=db))
        assert sorted(u.username for u in filtered) == ["m1"]
    finally:
        db.close()


# ── User PATCH / DELETE scoping ─────────────────────────────────────────────

def test_company_owner_cannot_patch_other_company_user(in_memory_db):
    from auth.router import update_user
    from auth.schemas import UserUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        c2 = _make_company(db, name="c2")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        target = _make_user(db, "m2", role="company_member", company_id=c2.id)

        with pytest.raises(HTTPException) as exc:
            _run(update_user(
                user_id=target.id,
                body=UserUpdate(display_name="hacked"),
                admin=co, db=db,
            ))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_company_owner_cannot_change_role(in_memory_db):
    from auth.router import update_user
    from auth.schemas import UserUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        m = _make_user(db, "m", role="company_member", company_id=c1.id)

        with pytest.raises(HTTPException) as exc:
            _run(update_user(
                user_id=m.id,
                body=UserUpdate(role="company_owner"),
                admin=co, db=db,
            ))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_company_owner_can_patch_member_in_own_company(in_memory_db):
    from auth.router import update_user
    from auth.schemas import UserUpdate

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        m = _make_user(db, "m", role="company_member", company_id=c1.id)

        out = _run(update_user(
            user_id=m.id,
            body=UserUpdate(display_name="renamed"),
            admin=co, db=db,
        ))
        assert out.display_name == "renamed"
    finally:
        db.close()


def test_company_owner_cannot_delete_company_owner_in_own_company(in_memory_db):
    from auth.router import delete_user

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co1 = _make_user(db, "co1", role="company_owner", company_id=c1.id)
        co2 = _make_user(db, "co2", role="company_owner", company_id=c1.id)
        with pytest.raises(HTTPException) as exc:
            _run(delete_user(user_id=co2.id, admin=co1, db=db))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_company_owner_can_delete_member_in_own_company(in_memory_db):
    from auth.router import delete_user

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        c1 = _make_company(db, name="c1")
        co = _make_user(db, "co", role="company_owner", company_id=c1.id)
        m = _make_user(db, "m", role="company_member", company_id=c1.id)
        result = _run(delete_user(user_id=m.id, admin=co, db=db))
        assert "無効化" in result["message"]
        db.refresh(m)
        assert m.is_active is False
    finally:
        db.close()
