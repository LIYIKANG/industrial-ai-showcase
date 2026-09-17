"""
core/quota.py
=============
Per-Company quota helpers: month-start (JST), monthly page usage,
active user count, and HTTPException-raising checks for routes.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.models import Company, ProcessingJob, User

_JST = ZoneInfo("Asia/Tokyo")
_UTC = ZoneInfo("UTC")


def month_window_jst() -> tuple[datetime, datetime]:
    """Returns (start, end) as naive UTC datetimes corresponding to the
    current calendar month in JST. The window is half-open [start, end)."""
    now_jst = datetime.now(_JST)
    start_jst = now_jst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # next month
    if start_jst.month == 12:
        end_jst = start_jst.replace(year=start_jst.year + 1, month=1)
    else:
        end_jst = start_jst.replace(month=start_jst.month + 1)
    # ProcessingJob.created_at is stored as naive UTC (datetime.utcnow), so convert.
    start_utc = start_jst.astimezone(_UTC).replace(tzinfo=None)
    end_utc = end_jst.astimezone(_UTC).replace(tzinfo=None)
    return start_utc, end_utc


def get_company_monthly_pages(db: Session, company_id: int) -> int:
    """Sum of page_count for non-failed jobs in current JST month."""
    start, end = month_window_jst()
    total = (
        db.query(func.coalesce(func.sum(ProcessingJob.page_count), 0))
        .filter(
            ProcessingJob.company_id == company_id,
            ProcessingJob.status != "failed",
            ProcessingJob.created_at >= start,
            ProcessingJob.created_at < end,
        )
        .scalar()
    )
    return int(total or 0)


def get_company_active_user_count(db: Session, company_id: int) -> int:
    return (
        db.query(User)
        .filter(User.company_id == company_id, User.is_active == True)  # noqa: E712
        .count()
    )


def check_page_quota_or_429(db: Session, user: User, pages_to_add: int) -> None:
    """Refuse job creation if it would exceed the Company's monthly page budget.
    super_admin (company_id=NULL) is exempt."""
    if user.role == "super_admin" or user.company_id is None:
        return
    company = db.query(Company).filter(Company.id == user.company_id).first()
    if company is None or not company.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="所属 Company が無効です。",
        )
    used = get_company_monthly_pages(db, company.id)
    if used + pages_to_add > company.max_monthly_pages:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"今月のページ数上限 ({company.max_monthly_pages}) に達しました。"
                f"使用済 {used} ページ / 申請 {pages_to_add} ページ。"
            ),
        )


def check_user_quota_or_429(db: Session, company_id: int) -> None:
    """Refuse new user creation if it would exceed the Company's max_users."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="指定された Company が存在しません。")
    if not company.is_active:
        raise HTTPException(status_code=403, detail="指定された Company は無効です。")
    current = get_company_active_user_count(db, company_id)
    if current >= company.max_users:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Company '{company.name}' のユーザー数上限 ({company.max_users}) に達しました。"
            ),
        )
