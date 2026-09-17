"""
core/billing.py
===============
Stripe 決済ヘルパー（Phase 1: サブスクのみ）。

- Stripe Customer ↔ Company（1:1）、Subscription ↔ Company（1件）。
- Checkout（ホスト型）でプラン契約、Billing Portal でプラン変更・解約。
- Webhook で Company.plan / subscription_status / 上限を Stripe から同期する。

真実の源は Stripe。本モジュールは Webhook イベントを受けて DB を追従させる。
Stripe 未設定（STRIPE_SECRET_KEY 空）でも import は可能で、API 呼び出し時にのみ
明示エラー（_require_stripe）を出す。
"""

import logging
from datetime import datetime
from typing import Optional

from auth import service
from auth.models import Company
from core.config import (
    BILLABLE_PLANS,
    PLAN_PRICE_IDS,
    PRICE_ID_TO_PLAN,
    settings,
)

logger = logging.getLogger(__name__)


class BillingNotConfigured(RuntimeError):
    """Stripe が未設定のまま課金 API を呼び出した。"""


def _get_stripe():
    """stripe を遅延 import し、api_key を反映して返す。

    stripe をモジュール import 時にロードすると常駐メモリが約 54MB 増える。
    本アプリは単一 uvicorn プロセスかつメモリ上限が小さいため、起動時の常駐を
    抑えるべく、課金 API を実際に呼ぶ関数内でのみ import する。
    """
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def _require_stripe():
    """Stripe 設定を検証し、遅延 import した stripe モジュールを返す。"""
    if not settings.is_stripe_configured:
        raise BillingNotConfigured("Stripe が設定されていません（STRIPE_SECRET_KEY 未設定）。")
    return _get_stripe()


# ── Customer / Checkout / Portal ───────────────────────────────────────────────

def get_or_create_customer(db, company: Company) -> str:
    """Company に紐づく Stripe Customer ID を返す。無ければ作成して保存する。"""
    stripe = _require_stripe()
    if company.stripe_customer_id:
        return company.stripe_customer_id
    customer = stripe.Customer.create(
        name=company.name,
        metadata={"company_id": str(company.id)},
    )
    company.stripe_customer_id = customer["id"]
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return company.stripe_customer_id


def create_checkout_session(db, company: Company, plan: str) -> str:
    """サブスク契約用の Checkout Session を作成し、リダイレクト URL を返す。"""
    stripe = _require_stripe()
    if plan not in BILLABLE_PLANS:
        raise ValueError(f"不正な、または課金対象外のプランです: {plan}")
    price_id = PLAN_PRICE_IDS.get(plan)
    if not price_id:
        raise ValueError(f"プラン '{plan}' の Stripe Price が未設定です（STRIPE_PRICE_{plan.upper()}）。")
    customer_id = get_or_create_customer(db, company)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(company.id),
        success_url=f"{settings.APP_BASE_URL}/me?billing=success",
        cancel_url=f"{settings.APP_BASE_URL}/me?billing=cancel",
        allow_promotion_codes=True,
    )
    return session["url"]


def create_portal_session(db, company: Company) -> str:
    """Billing Portal セッションを作成し、URL を返す（プラン変更・解約・カード更新）。"""
    stripe = _require_stripe()
    if not company.stripe_customer_id:
        raise ValueError("この Company には Stripe 顧客が紐付いていません。先に契約してください。")
    session = stripe.billing_portal.Session.create(
        customer=company.stripe_customer_id,
        return_url=f"{settings.APP_BASE_URL}/me",
    )
    return session["url"]


# ── Webhook 同期 ───────────────────────────────────────────────────────────────

def construct_event(payload: bytes, sig_header: str):
    """Stripe-Signature を検証して Event を返す。検証失敗は例外を投げる。"""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise BillingNotConfigured("STRIPE_WEBHOOK_SECRET が未設定です。")
    stripe = _get_stripe()
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    )


def _find_company(
    db,
    *,
    company_id=None,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> Optional[Company]:
    """client_reference_id → subscription_id → customer_id の順で Company を解決する。"""
    if company_id:
        try:
            c = db.query(Company).filter(Company.id == int(company_id)).first()
        except (TypeError, ValueError):
            c = None
        if c:
            return c
    if subscription_id:
        c = db.query(Company).filter(Company.stripe_subscription_id == subscription_id).first()
        if c:
            return c
    if customer_id:
        c = db.query(Company).filter(Company.stripe_customer_id == customer_id).first()
        if c:
            return c
    return None


def _sync_from_subscription(db, sub, *, company_id=None, customer_id=None) -> None:
    """Stripe Subscription オブジェクト（dict 互換）から Company を更新する。"""
    customer_id = customer_id or sub.get("customer")
    subscription_id = sub.get("id")
    status = sub.get("status") or "inactive"

    items = (sub.get("items") or {}).get("data") or []
    first_item = items[0] if items else {}
    price_id = (first_item.get("price") or {}).get("id") if first_item else None

    # Stripe "basil" API（2025-03-31以降）では current_period_end が Subscription
    # ルートから items 配下へ移動。旧版互換のためルート→item の順でフォールバック。
    cpe = sub.get("current_period_end") or first_item.get("current_period_end")
    current_period_end = datetime.utcfromtimestamp(cpe) if cpe else None

    company = _find_company(
        db,
        company_id=company_id,
        customer_id=customer_id,
        subscription_id=subscription_id,
    )
    if company is None:
        logger.warning(
            "[billing] subscription event for unknown company "
            "(sub=%s customer=%s ref=%s)", subscription_id, customer_id, company_id,
        )
        return

    updates: dict = {
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "subscription_status": status,
        "current_period_end": current_period_end,
    }
    # 有効な購読のみプランを切替（PLAN_LIMITS の上限も自動適用：service.update_company）。
    # 解約・支払い遅延時は plan / 上限を据え置き（Phase 1 は非破壊）。
    if status in ("active", "trialing"):
        plan = PRICE_ID_TO_PLAN.get(price_id)
        if plan:
            updates["plan"] = plan
        elif price_id:
            logger.warning("[billing] unknown price_id=%s — plan unchanged", price_id)

    service.update_company(db, company, **updates)
    logger.info(
        "[billing] synced company id=%s plan=%s status=%s",
        company.id, company.plan, status,
    )


def apply_subscription_event(db, event) -> None:
    """検証済み Webhook Event を受け取り、関連する Company を同期する。"""
    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        subscription_id = obj.get("subscription")
        if not subscription_id:
            return  # 一回払い等（Phase 1 ではサブスクのみ）
        sub = _get_stripe().Subscription.retrieve(subscription_id)
        _sync_from_subscription(
            db, sub,
            company_id=obj.get("client_reference_id"),
            customer_id=obj.get("customer"),
        )

    elif etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _sync_from_subscription(db, obj)

    elif etype == "invoice.payment_failed":
        company = _find_company(db, customer_id=obj.get("customer"))
        if company is not None:
            service.update_company(db, company, subscription_status="past_due")
            logger.info("[billing] company id=%s marked past_due", company.id)

    else:
        logger.debug("[billing] ignored event type=%s", etype)
