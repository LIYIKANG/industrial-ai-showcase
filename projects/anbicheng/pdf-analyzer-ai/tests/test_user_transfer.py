"""
tests/test_user_transfer.py
===========================
Tests for PR5: PATCH /api/admin/users/{user_id}/transfer

Direct route-handler invocation pattern (same as test_company_admin_api.py).
Covers:
- super_admin transfers company_member from Co-A to Co-B → success
- transfer to same Company → no-op success
- transfer to non-existent Company → 404
- transfer to inactive Company → 403
- transfer fails when destination at max_users → 409
- transfer of super_admin user → 400
- transfer of last active company_owner → 409
- audit log entry written
- past ProcessingJob.company_id preserved (historical boundary)
"""

import asyncio
from datetime import datetime

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


def _make_job(db, username, company_id, file_id="job-1", page_count=3):
    from auth.models import ProcessingJob

    j = ProcessingJob(
        file_id=file_id,
        username=username,
        filename="doc.pdf",
        fields_filled=10,
        fields_total=10,
        page_count=page_count,
        extracted_json="[]",
        source_type="standard",
        status="done",
        company_id=company_id,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


# ── tests ───────────────────────────────────────────────────────────────────

def test_super_admin_transfers_member_between_companies(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B")
        m = _make_user(db, "m1", role="company_member", company_id=a.id)

        out = _run(admin_transfer_user(
            user_id=m.id,
            body=UserTransferRequest(company_id=b.id),
            admin=sa, db=db,
        ))
        assert out.company_id == b.id
        assert out.company_name == "Co-B"
        db.refresh(m)
        assert m.company_id == b.id
    finally:
        db.close()


def test_transfer_to_same_company_is_noop(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        m = _make_user(db, "m1", role="company_member", company_id=a.id)
        before_updated = m.updated_at

        out = _run(admin_transfer_user(
            user_id=m.id,
            body=UserTransferRequest(company_id=a.id),
            admin=sa, db=db,
        ))
        assert out.company_id == a.id
        db.refresh(m)
        # no commit path → updated_at unchanged
        assert m.updated_at == before_updated
    finally:
        db.close()


def test_transfer_to_nonexistent_company_404(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        m = _make_user(db, "m1", role="company_member", company_id=a.id)

        with pytest.raises(HTTPException) as exc:
            _run(admin_transfer_user(
                user_id=m.id,
                body=UserTransferRequest(company_id=9999),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_transfer_to_inactive_company_403(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        dead = _make_company(db, name="Co-Dead", is_active=False)
        m = _make_user(db, "m1", role="company_member", company_id=a.id)

        with pytest.raises(HTTPException) as exc:
            _run(admin_transfer_user(
                user_id=m.id,
                body=UserTransferRequest(company_id=dead.id),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 403
    finally:
        db.close()


def test_transfer_fails_when_destination_full_409(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A", max_users=5)
        b = _make_company(db, name="Co-B", max_users=2)
        # Fill destination to capacity
        _make_user(db, "b1", role="company_owner", company_id=b.id)
        _make_user(db, "b2", role="company_member", company_id=b.id)
        m = _make_user(db, "m1", role="company_member", company_id=a.id)

        with pytest.raises(HTTPException) as exc:
            _run(admin_transfer_user(
                user_id=m.id,
                body=UserTransferRequest(company_id=b.id),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 409
        assert "上限" in exc.value.detail
    finally:
        db.close()


def test_transfer_of_super_admin_user_400(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        target_sa = _make_user(db, "sa2", role="super_admin", company_id=None)
        b = _make_company(db, name="Co-B")

        with pytest.raises(HTTPException) as exc:
            _run(admin_transfer_user(
                user_id=target_sa.id,
                body=UserTransferRequest(company_id=b.id),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_transfer_of_last_active_company_owner_409(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B")
        # Only one active owner in Co-A
        owner = _make_user(db, "owner-a", role="company_owner", company_id=a.id)
        # There is an inactive owner in Co-A → still doesn't count
        _make_user(db, "owner-a-old", role="company_owner",
                   company_id=a.id, is_active=False)

        with pytest.raises(HTTPException) as exc:
            _run(admin_transfer_user(
                user_id=owner.id,
                body=UserTransferRequest(company_id=b.id),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 409
        assert "最後" in exc.value.detail
    finally:
        db.close()


def test_transfer_company_owner_succeeds_if_another_owner_remains(in_memory_db):
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B")
        owner1 = _make_user(db, "owner1", role="company_owner", company_id=a.id)
        _make_user(db, "owner2", role="company_owner", company_id=a.id)

        out = _run(admin_transfer_user(
            user_id=owner1.id,
            body=UserTransferRequest(company_id=b.id),
            admin=sa, db=db,
        ))
        assert out.company_id == b.id
    finally:
        db.close()


def test_audit_log_written_on_transfer(in_memory_db):
    from auth.models import AuditLog
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B")
        m = _make_user(db, "m1", role="company_member", company_id=a.id)

        _run(admin_transfer_user(
            user_id=m.id,
            body=UserTransferRequest(company_id=b.id),
            admin=sa, db=db,
        ))
        log = (
            db.query(AuditLog)
            .filter(AuditLog.action == "user_transfer")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert log is not None
        assert log.username == "sa"
        assert f"user_id={m.id}" in log.detail
        assert f"from_company={a.id}" in log.detail
        assert f"to_company={b.id}" in log.detail
    finally:
        db.close()


def test_transfer_does_not_modify_past_processing_jobs(in_memory_db):
    from auth.models import ProcessingJob
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B")
        m = _make_user(db, "m1", role="company_member", company_id=a.id)
        # Insert a job tied to Co-A BEFORE transfer
        job = _make_job(db, username="m1", company_id=a.id,
                        file_id="hist-1", page_count=5)
        assert job.company_id == a.id

        _run(admin_transfer_user(
            user_id=m.id,
            body=UserTransferRequest(company_id=b.id),
            admin=sa, db=db,
        ))

        db.refresh(m)
        assert m.company_id == b.id
        # historical job's company_id must be unchanged
        reloaded = db.query(ProcessingJob).filter(
            ProcessingJob.file_id == "hist-1"
        ).first()
        assert reloaded is not None
        assert reloaded.company_id == a.id
    finally:
        db.close()


def test_transfer_inactive_user_does_not_consume_quota(in_memory_db):
    """Inactive target should be transferable even if destination is at user quota,
    because active_user_count is unchanged."""
    from auth.router import admin_transfer_user
    from auth.schemas import UserTransferRequest

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        a = _make_company(db, name="Co-A")
        b = _make_company(db, name="Co-B", max_users=1)
        _make_user(db, "b1", role="company_owner", company_id=b.id)  # fills b
        inactive_m = _make_user(db, "m1", role="company_member",
                                company_id=a.id, is_active=False)

        out = _run(admin_transfer_user(
            user_id=inactive_m.id,
            body=UserTransferRequest(company_id=b.id),
            admin=sa, db=db,
        ))
        assert out.company_id == b.id
    finally:
        db.close()


def test_non_super_admin_cannot_transfer(in_memory_db):
    """company_owner is blocked by require_super_admin dependency."""
    from auth.dependencies import require_super_admin
    from auth.models import User

    co = User(
        username="co", display_name="CO", hashed_password="x",
        role="company_owner", company_id=1,
    )
    with pytest.raises(HTTPException) as exc:
        require_super_admin(current_user=co)
    assert exc.value.status_code == 403
