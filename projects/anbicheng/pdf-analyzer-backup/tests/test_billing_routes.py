"""
tests/test_billing_routes.py
============================
Tests for the Stripe billing HTTP endpoints (api/billing_routes.py):

  GET  /api/billing/subscription  → 購読状態＋プラン一覧
  POST /api/billing/checkout      → Checkout Session URL（権限・二重契約防止・未設定）
  POST /api/billing/portal        → Billing Portal URL（顧客未紐付けエラー）

We invoke the route handlers directly (in line with test_company_admin_api.py /
test_role_migration.py) rather than through FastAPI's TestClient. This keeps tests
fast and avoids spinning up the full app (which would require CLAUDE_API_KEY etc).

Stripe is never touched: core.billing.create_checkout_session / create_portal_session
are monkeypatched, and is_stripe_configured is toggled via settings.STRIPE_SECRET_KEY.
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


@pytest.fixture()
def stripe_on(monkeypatch):
    """Make settings.is_stripe_configured True (property derives from STRIPE_SECRET_KEY)."""
    from core.config import settings

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")
    return settings


@pytest.fixture()
def stripe_off(monkeypatch):
    """Make settings.is_stripe_configured False."""
    from core.config import settings

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    return settings


# ── helpers ─────────────────────────────────────────────────────────────────

def _run(coro_or_value):
    """Await coroutines, return values as-is (handlers may be sync or async)."""
    if asyncio.iscoroutine(coro_or_value):
        return asyncio.get_event_loop().run_until_complete(coro_or_value)
    return coro_or_value


def _make_company(db, name="acme", plan="basic", max_users=5,
                  max_monthly_pages=1500, is_active=True, **kwargs):
    from auth.models import Company

    c = Company(
        name=name, plan=plan,
        max_users=max_users, max_monthly_pages=max_monthly_pages,
        is_active=is_active, **kwargs,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_user(db, username, role, company_id, is_active=True):
    from auth.models import User

    u = User(
        username=username,
        display_name=username,
        hashed_password="x",
        role=role,
        company_id=company_id,
        is_active=is_active,
        allowed_pages=["jusetsu"],
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ── GET /subscription ────────────────────────────────────────────────────────

def test_subscription_company_owner_gets_status_and_plans(in_memory_db, stripe_on):
    from api.billing_routes import get_subscription

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(db, name="acme", plan="standard",
                          subscription_status="active",
                          stripe_customer_id="cus_1")
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)

        out = _run(get_subscription(current_user=owner, db=db))

        assert out["company_id"] == c.id
        assert out["plan"] == "standard"
        assert out["subscription_status"] == "active"
        assert out["has_stripe_customer"] is True
        assert out["can_manage"] is True
        assert out["stripe_configured"] is True
        # plan list covers all billable plans with limits
        plan_names = [p["plan"] for p in out["available_plans"]]
        assert plan_names == ["basic", "standard", "pro", "flagship"]
        basic = next(p for p in out["available_plans"] if p["plan"] == "basic")
        assert basic["max_users"] == 2
        assert basic["max_monthly_pages"] == 50 * 30
    finally:
        db.close()


def test_subscription_super_admin_no_company(in_memory_db, stripe_off):
    from api.billing_routes import get_subscription

    db = in_memory_db["SessionLocal"]()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)

        out = _run(get_subscription(current_user=sa, db=db))

        assert out["company_id"] is None
        assert out["plan"] is None
        assert out["subscription_status"] is None
        assert out["can_manage"] is True            # super_admin manages
        assert out["stripe_configured"] is False     # stripe_off
        assert [p["plan"] for p in out["available_plans"]] == [
            "basic", "standard", "pro", "flagship"
        ]
    finally:
        db.close()


# ── POST /checkout ─────────────────────────────────────────────────────────────

def test_checkout_company_owner_success(in_memory_db, stripe_on, monkeypatch):
    from api import billing_routes
    from api.billing_routes import create_checkout, CheckoutRequest

    monkeypatch.setattr(
        billing_routes.billing, "create_checkout_session",
        lambda db, company, plan: "https://checkout.stripe.test/session_abc",
    )

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(db, name="acme", plan="basic")
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)

        out = _run(create_checkout(
            body=CheckoutRequest(plan="standard"),
            admin=owner, db=db,
        ))
        assert out == {"url": "https://checkout.stripe.test/session_abc"}
    finally:
        db.close()


def test_checkout_operator_forbidden(in_memory_db, stripe_on):
    from auth.dependencies import require_company_admin
    from auth.models import User

    # require_company_admin is the gate; operator / company_member never pass.
    for role in ("operator", "company_member"):
        u = User(
            username=role, display_name=role, hashed_password="x",
            role=role, company_id=1,
        )
        with pytest.raises(HTTPException) as exc:
            require_company_admin(current_user=u)
        assert exc.value.status_code == 403


def test_checkout_super_admin_without_company_id_400(in_memory_db, stripe_on):
    from api.billing_routes import create_checkout, CheckoutRequest

    db = in_memory_db["SessionLocal"]()
    try:
        sa = _make_user(db, "sa", role="super_admin", company_id=None)
        with pytest.raises(HTTPException) as exc:
            _run(create_checkout(
                body=CheckoutRequest(plan="standard", company_id=None),
                admin=sa, db=db,
            ))
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_checkout_double_subscription_conflict_409(in_memory_db, stripe_on, monkeypatch):
    from api import billing_routes
    from api.billing_routes import create_checkout, CheckoutRequest

    # If reached, the mock would succeed; the 409 must fire *before* this.
    monkeypatch.setattr(
        billing_routes.billing, "create_checkout_session",
        lambda db, company, plan: "https://should.not.be.called",
    )

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(
            db, name="acme", plan="standard",
            subscription_status="active", stripe_subscription_id="sub_1",
        )
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)
        with pytest.raises(HTTPException) as exc:
            _run(create_checkout(
                body=CheckoutRequest(plan="pro"),
                admin=owner, db=db,
            ))
        assert exc.value.status_code == 409
    finally:
        db.close()


def test_checkout_stripe_not_configured_503(in_memory_db, stripe_off):
    from api.billing_routes import create_checkout, CheckoutRequest

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(db, name="acme", plan="basic")
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)
        with pytest.raises(HTTPException) as exc:
            _run(create_checkout(
                body=CheckoutRequest(plan="standard"),
                admin=owner, db=db,
            ))
        assert exc.value.status_code == 503
    finally:
        db.close()


# ── POST /portal ───────────────────────────────────────────────────────────────

def test_portal_company_owner_success(in_memory_db, stripe_on, monkeypatch):
    from api import billing_routes
    from api.billing_routes import create_portal, PortalRequest

    monkeypatch.setattr(
        billing_routes.billing, "create_portal_session",
        lambda db, company: "https://portal.stripe.test/session_xyz",
    )

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(db, name="acme", stripe_customer_id="cus_1")
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)

        out = _run(create_portal(
            body=PortalRequest(),
            admin=owner, db=db,
        ))
        assert out == {"url": "https://portal.stripe.test/session_xyz"}
    finally:
        db.close()


def test_portal_without_stripe_customer_400(in_memory_db, stripe_on, monkeypatch):
    from api import billing_routes
    from api.billing_routes import create_portal, PortalRequest

    # Delegate to the real ValueError path: no stripe_customer_id → ValueError → 400.
    def _raise(db, company):
        raise ValueError("この Company には Stripe 顧客が紐付いていません。")

    monkeypatch.setattr(billing_routes.billing, "create_portal_session", _raise)

    db = in_memory_db["SessionLocal"]()
    try:
        c = _make_company(db, name="acme", stripe_customer_id=None)
        owner = _make_user(db, "co", role="company_owner", company_id=c.id)
        with pytest.raises(HTTPException) as exc:
            _run(create_portal(
                body=PortalRequest(),
                admin=owner, db=db,
            ))
        assert exc.value.status_code == 400
    finally:
        db.close()
