"""
api/billing_routes.py
=====================
Stripe 決済ルーター（Phase 1: サブスクのみ）。

  POST /api/billing/checkout      → Checkout Session を作成し URL を返す（company_owner）
  POST /api/billing/portal        → Billing Portal Session を作成し URL を返す（company_owner）
  GET  /api/billing/subscription  → 所属 Company の購読状態＋契約可能プラン一覧
  POST /api/billing/webhook       → Stripe Webhook（公開・署名検証）

課金主体は Company（テナント）。company_owner が自社のみ操作可能。
super_admin は対象 company_id を明示指定する。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import dependencies as deps
from auth import service
from auth.models import Company, User
from core import billing
from core.config import BILLABLE_PLANS, PLAN_LIMITS, PLAN_PRICE_IDS, settings
from core.database import get_db

logger = logging.getLogger(__name__)

billing_router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan: str
    company_id: int | None = None  # super_admin が対象を指定する場合のみ


class PortalRequest(BaseModel):
    company_id: int | None = None  # super_admin が対象を指定する場合のみ


def _resolve_company(db: Session, user: User, company_id: int | None) -> Company:
    """操作対象の Company を権限に応じて解決する。

    - super_admin / root_admin: company_id 必須（自身は Company 無所属のため）。
    - company_owner: 自社のみ（company_id は無視）。
    """
    if user.role in ("super_admin", "root_admin"):
        if company_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="システム管理者は company_id の指定が必要です。",
            )
        company = service.get_company(db, company_id)
        if company is None:
            raise HTTPException(status_code=404, detail="指定された Company が存在しません。")
        return company
    # company_owner
    if user.company_id is None:
        raise HTTPException(status_code=403, detail="所属 Company がありません。")
    company = service.get_company(db, user.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="所属 Company が見つかりません。")
    return company


@billing_router.post("/checkout")
def create_checkout(
    body: CheckoutRequest,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    if not settings.is_stripe_configured:
        raise HTTPException(status_code=503, detail="決済は現在利用できません（未設定）。")
    company = _resolve_company(db, admin, body.company_id)
    # 二重サブスク防止：既に有効な購読があるならプラン変更は Portal へ誘導
    if company.subscription_status in ("active", "trialing") and company.stripe_subscription_id:
        raise HTTPException(
            status_code=409,
            detail="既にサブスクリプション契約中です。プラン変更は「お支払い管理」から行ってください。",
        )
    try:
        url = billing.create_checkout_session(db, company, body.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    service.add_audit(db, admin.username, "billing_checkout", "", f"company={company.id} plan={body.plan}")
    return {"url": url}


@billing_router.post("/portal")
def create_portal(
    body: PortalRequest,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    if not settings.is_stripe_configured:
        raise HTTPException(status_code=503, detail="決済は現在利用できません（未設定）。")
    company = _resolve_company(db, admin, body.company_id)
    try:
        url = billing.create_portal_session(db, company)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    service.add_audit(db, admin.username, "billing_portal", "", f"company={company.id}")
    return {"url": url}


@billing_router.get("/subscription")
def get_subscription(
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    """所属 Company の購読状態＋契約可能プラン一覧を返す（/me 表示用）。"""
    plans = [
        {
            "plan": p,
            "max_users": PLAN_LIMITS[p]["max_users"],
            "max_monthly_pages": PLAN_LIMITS[p]["max_monthly_pages"],
            "price_configured": bool(PLAN_PRICE_IDS.get(p)),
        }
        for p in BILLABLE_PLANS
    ]
    if current_user.role in ("super_admin", "root_admin") or current_user.company_id is None:
        return {
            "company_id": None,
            "plan": None,
            "subscription_status": None,
            "current_period_end": None,
            "can_manage": current_user.role in ("super_admin", "root_admin"),
            "stripe_configured": settings.is_stripe_configured,
            "available_plans": plans,
        }
    company = service.get_company(db, current_user.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="所属 Company が見つかりません。")
    return {
        "company_id": company.id,
        "plan": company.plan,
        "subscription_status": company.subscription_status,
        "current_period_end": (
            company.current_period_end.isoformat() + "Z" if company.current_period_end else None
        ),
        "has_stripe_customer": bool(company.stripe_customer_id),
        "can_manage": current_user.role == "company_owner",
        "stripe_configured": settings.is_stripe_configured,
        "available_plans": plans,
    }


@billing_router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Stripe Webhook 受信口（公開・認証なし）。署名を検証して購読を同期する。"""
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    if not sig_header:
        raise HTTPException(status_code=400, detail="署名ヘッダーがありません。")
    try:
        event = billing.construct_event(payload, sig_header)
    except billing.BillingNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook が未設定です。")
    except Exception as e:  # noqa: BLE001 - 署名検証失敗 / 不正ペイロード
        logger.warning("[billing] webhook verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Webhook の検証に失敗しました。")
    try:
        billing.apply_subscription_event(db, event)
    except Exception:  # noqa: BLE001 - 個別イベント失敗で 500 を返すと Stripe が再送し続けるため握りつぶしログ
        logger.exception("[billing] failed to apply event type=%s", event.get("type"))
    return {"received": True}
