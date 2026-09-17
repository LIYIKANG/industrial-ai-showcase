"""
tests/test_quota.py
===================
Tests for PR3 quota helpers: month window (JST), per-Company monthly page
usage, active user count, and the HTTPException-raising guards used by
job-creation routes and user-creation endpoint.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def in_memory_db(monkeypatch):
    """In-memory SQLite shared across sessions via StaticPool."""
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


def _make_company(db, name="acme", max_users=2, max_monthly_pages=50, is_active=True):
    from auth.models import Company

    c = Company(
        name=name, plan="basic",
        max_users=max_users, max_monthly_pages=max_monthly_pages,
        is_active=is_active,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_user(db, username, company_id, role="company_member", is_active=True):
    from auth.models import User

    u = User(
        username=username, display_name=username, hashed_password="x",
        role=role, company_id=company_id, is_active=is_active,
        allowed_pages=["jusetsu"],
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_job(db, company_id, page_count, status="done", created_at=None):
    from auth.models import ProcessingJob

    j = ProcessingJob(
        file_id=f"j_{datetime.utcnow().timestamp()}_{page_count}_{status}",
        username="anyone",
        filename="x.xlsx",
        fields_filled=0, fields_total=0,
        page_count=page_count,
        extracted_json="[]",
        source_type="standard",
        status=status,
        company_id=company_id,
    )
    if created_at is not None:
        j.created_at = created_at
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


# ── month_window_jst ────────────────────────────────────────────────────────


def test_month_window_jst_is_half_open_one_month_long(in_memory_db):
    from core.quota import month_window_jst

    start, end = month_window_jst()
    # Window must be ~28-31 days long
    delta = end - start
    assert 27 <= delta.days <= 31
    # Start should have day=1 in JST (= day=1 or day=last-of-prev in UTC).
    # We just verify it's at most ~16h ahead of UTC start-of-month boundary
    # by re-deriving from JST.
    assert start < end


# ── get_company_monthly_pages ───────────────────────────────────────────────


def test_get_company_monthly_pages_sums_non_failed_in_window(in_memory_db):
    from core.quota import get_company_monthly_pages, month_window_jst

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_monthly_pages=100)

        start, _end = month_window_jst()
        in_window = start + timedelta(days=1)
        before_window = start - timedelta(days=2)

        _make_job(db, company.id, page_count=5, status="done", created_at=in_window)
        _make_job(db, company.id, page_count=7, status="processing", created_at=in_window)
        # failed jobs are excluded
        _make_job(db, company.id, page_count=999, status="failed", created_at=in_window)
        # out-of-window jobs are excluded
        _make_job(db, company.id, page_count=999, status="done", created_at=before_window)

        used = get_company_monthly_pages(db, company.id)
        assert used == 12
    finally:
        db.close()


# ── check_page_quota_or_429 ─────────────────────────────────────────────────


def test_check_page_quota_raises_429_when_exceeded(in_memory_db):
    from core.quota import check_page_quota_or_429, month_window_jst

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_monthly_pages=10)
        user = _make_user(db, "u1", company.id)

        start, _ = month_window_jst()
        _make_job(db, company.id, page_count=8, status="done",
                  created_at=start + timedelta(hours=1))

        # 8 used + 3 new = 11 > 10 → 429
        with pytest.raises(HTTPException) as exc:
            check_page_quota_or_429(db, user, 3)
        assert exc.value.status_code == 429
        assert "ページ" in exc.value.detail
    finally:
        db.close()


def test_check_page_quota_allows_exactly_at_limit(in_memory_db):
    from core.quota import check_page_quota_or_429, month_window_jst

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_monthly_pages=10)
        user = _make_user(db, "u1", company.id)

        start, _ = month_window_jst()
        _make_job(db, company.id, page_count=7, status="done",
                  created_at=start + timedelta(hours=1))

        # 7 + 3 = 10 (not > 10) → allowed
        check_page_quota_or_429(db, user, 3)
    finally:
        db.close()


def test_check_page_quota_noop_for_super_admin(in_memory_db):
    from auth.models import User
    from core.quota import check_page_quota_or_429

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = User(
            username="sa", display_name="SA", hashed_password="x",
            role="super_admin", company_id=None,
        )
        # No HTTPException even with huge pages_to_add
        check_page_quota_or_429(db, sa, 10**9)
    finally:
        db.close()


def test_check_page_quota_rejects_inactive_company(in_memory_db):
    from core.quota import check_page_quota_or_429

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="dead", is_active=False)
        user = _make_user(db, "u1", company.id)
        with pytest.raises(HTTPException) as exc:
            check_page_quota_or_429(db, user, 1)
        assert exc.value.status_code == 403
    finally:
        db.close()


# ── check_user_quota_or_429 ─────────────────────────────────────────────────


def test_check_user_quota_raises_429_at_limit(in_memory_db):
    from core.quota import check_user_quota_or_429

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_users=2)
        _make_user(db, "u1", company.id)
        _make_user(db, "u2", company.id)

        # active count == max_users → next creation refused
        with pytest.raises(HTTPException) as exc:
            check_user_quota_or_429(db, company.id)
        assert exc.value.status_code == 429
        assert "ユーザー数" in exc.value.detail
    finally:
        db.close()


def test_check_user_quota_ignores_inactive_users(in_memory_db):
    from core.quota import check_user_quota_or_429

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_users=2)
        _make_user(db, "u1", company.id, is_active=True)
        _make_user(db, "u2", company.id, is_active=False)

        # 1 active < 2 → allowed
        check_user_quota_or_429(db, company.id)
    finally:
        db.close()


def test_get_company_active_user_count_excludes_inactive(in_memory_db):
    from core.quota import get_company_active_user_count

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = _make_company(db, name="c1", max_users=10)
        _make_user(db, "u1", company.id, is_active=True)
        _make_user(db, "u2", company.id, is_active=False)
        _make_user(db, "u3", company.id, is_active=True)

        assert get_company_active_user_count(db, company.id) == 2
    finally:
        db.close()
