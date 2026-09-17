"""
tests/test_billing.py
=====================
Tests for Stripe webhook → Company sync (core.billing.apply_subscription_event).
Network is never touched: we feed plain-dict events (as Stripe sends) and
override the price→plan map. Stripe.Subscription.retrieve is only used by the
checkout.session.completed branch, which is not exercised here.
"""

from datetime import datetime

import pytest
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
    yield {"SessionLocal": TestSessionLocal}
    db_module.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def _make_company(db, **kwargs):
    from auth.models import Company

    defaults = dict(
        name="acme", plan="basic", max_users=2, max_monthly_pages=50, is_active=True,
    )
    defaults.update(kwargs)
    c = Company(**defaults)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _subscription_obj(sub_id, customer, price_id, status, period_end=None, period_end_on="root"):
    """Build a Subscription-like dict.

    period_end_on="root"  → legacy (pre-basil) shape
    period_end_on="item"  → Stripe basil shape (current_period_end lives on the item)
    """
    item = {"price": {"id": price_id}}
    sub = {
        "id": sub_id,
        "customer": customer,
        "status": status,
        "items": {"data": [item]},
    }
    if period_end is not None:
        if period_end_on == "item":
            item["current_period_end"] = period_end
        else:
            sub["current_period_end"] = period_end
    return sub


# ── active subscription switches plan + applies PLAN_LIMITS ──────────────────

def test_active_subscription_switches_plan_and_limits(in_memory_db, monkeypatch):
    from core import billing
    from core.config import PLAN_LIMITS

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {"price_std": "standard"})

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(db, plan="basic", stripe_customer_id="cus_1")
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": _subscription_obj(
                "sub_1", "cus_1", "price_std", "active", period_end=1_900_000_000,
            )},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)

        assert company.plan == "standard"
        assert company.subscription_status == "active"
        assert company.stripe_subscription_id == "sub_1"
        assert company.max_monthly_pages == PLAN_LIMITS["standard"]["max_monthly_pages"]
        assert company.max_users == PLAN_LIMITS["standard"]["max_users"]
        assert company.current_period_end == datetime.utcfromtimestamp(1_900_000_000)
    finally:
        db.close()


# ── basil API: current_period_end read from the subscription item ────────────

def test_period_end_read_from_item_basil_shape(in_memory_db, monkeypatch):
    from core import billing

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {"price_std": "standard"})

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(db, plan="basic", stripe_customer_id="cus_1")
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": _subscription_obj(
                "sub_1", "cus_1", "price_std", "active",
                period_end=1_900_000_000, period_end_on="item",
            )},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)
        assert company.current_period_end == datetime.utcfromtimestamp(1_900_000_000)
    finally:
        db.close()


# ── company resolved by subscription_id when customer differs ────────────────

def test_resolved_by_subscription_id(in_memory_db, monkeypatch):
    from core import billing

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {"price_pro": "pro"})

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(db, plan="basic", stripe_subscription_id="sub_42")
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": _subscription_obj("sub_42", "cus_unknown", "price_pro", "active")},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)
        assert company.plan == "pro"
        # customer id is back-filled from the event
        assert company.stripe_customer_id == "cus_unknown"
    finally:
        db.close()


# ── cancel keeps plan/limits, only flips status (Phase 1 non-breaking) ───────

def test_cancel_keeps_plan_and_limits(in_memory_db, monkeypatch):
    from core import billing

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {"price_std": "standard"})

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(
            db, plan="standard", max_monthly_pages=3000, max_users=3,
            stripe_subscription_id="sub_1", stripe_customer_id="cus_1",
            subscription_status="active",
        )
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": _subscription_obj("sub_1", "cus_1", "price_std", "canceled")},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)
        assert company.subscription_status == "canceled"
        assert company.plan == "standard"          # unchanged
        assert company.max_monthly_pages == 3000   # unchanged
    finally:
        db.close()


# ── unknown price never changes the plan ─────────────────────────────────────

def test_unknown_price_keeps_plan(in_memory_db, monkeypatch):
    from core import billing

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {})  # nothing maps

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(db, plan="basic", stripe_customer_id="cus_1")
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": _subscription_obj("sub_1", "cus_1", "price_???", "active")},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)
        assert company.plan == "basic"             # unchanged
        assert company.subscription_status == "active"
    finally:
        db.close()


# ── payment failure flips status to past_due ─────────────────────────────────

def test_payment_failed_marks_past_due(in_memory_db):
    from core import billing

    db = in_memory_db["SessionLocal"]()
    try:
        company = _make_company(db, stripe_customer_id="cus_1", subscription_status="active")
        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_1"}},
        }
        billing.apply_subscription_event(db, event)
        db.refresh(company)
        assert company.subscription_status == "past_due"
    finally:
        db.close()


# ── unknown company is ignored (no crash) ────────────────────────────────────

def test_unknown_company_is_ignored(in_memory_db, monkeypatch):
    from core import billing

    monkeypatch.setattr(billing, "PRICE_ID_TO_PLAN", {"price_std": "standard"})

    db = in_memory_db["SessionLocal"]()
    try:
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": _subscription_obj("sub_x", "cus_nope", "price_std", "active")},
        }
        # must not raise
        billing.apply_subscription_event(db, event)
    finally:
        db.close()
