"""
auth/service.py
===============
ビジネスロジック：パスワードハッシュ、JWT 発行/検証、ユーザー CRUD、
ロックアウト判定、監査ログ、トークンブラックリスト。
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from auth.models import ALL_PAGES, AuditLog, LoginAttempt, TokenBlacklist, User
from core.config import settings

# ── パスワード ────────────────────────────────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────

def _build_token(subject: str, token_type: str, expires: timedelta) -> Tuple[str, str]:
    """JWT を生成し (token, jti) を返す。"""
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "jti": jti,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + expires,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_access_token(username: str) -> str:
    token, _ = _build_token(
        username, "access", timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES)
    )
    return token


def create_refresh_token(username: str) -> Tuple[str, str]:
    """(token, jti) を返す。"""
    return _build_token(
        username, "refresh", timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ── ユーザー CRUD ─────────────────────────────────────────────────────────────

def get_user(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list:
    return db.query(User).order_by(User.created_at).all()


def create_user(
    db: Session,
    username: str,
    display_name: str,
    password: str,
    role: str = "company_member",
    must_change_password: bool = True,
    allowed_pages: list | None = None,
    company_id: Optional[int] = None,
) -> User:
    user = User(
        username=username,
        display_name=display_name,
        hashed_password=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=must_change_password,
        allowed_pages=allowed_pages if allowed_pages is not None else list(ALL_PAGES),
        company_id=company_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user: User, **kwargs) -> User:
    for key, value in kwargs.items():
        if key == "password":
            user.hashed_password = hash_password(value)
        else:
            setattr(user, key, value)
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


# ── ログイン制限 ───────────────────────────────────────────────────────────────

def is_locked(db: Session, username: str) -> bool:
    """直近 LOGIN_LOCKOUT_MINUTES 分以内に MAX_LOGIN_ATTEMPTS 回失敗 → ロック。"""
    since = datetime.utcnow() - timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    count = (
        db.query(LoginAttempt)
        .filter(
            LoginAttempt.username == username,
            LoginAttempt.success == False,  # noqa: E712
            LoginAttempt.created_at >= since,
        )
        .count()
    )
    return count >= settings.MAX_LOGIN_ATTEMPTS


def record_attempt(db: Session, username: str, ip: str, success: bool) -> None:
    db.add(LoginAttempt(username=username, ip_address=ip, success=success))
    db.commit()


# ── 監査ログ ──────────────────────────────────────────────────────────────────

def add_audit(db: Session, username: str, action: str, ip: str, detail: str = "") -> None:
    db.add(AuditLog(username=username, action=action, ip_address=ip, detail=detail))
    db.commit()


# ── トークンブラックリスト ────────────────────────────────────────────────────

def blacklist_jti(db: Session, jti: str, expires_at: datetime) -> None:
    db.add(TokenBlacklist(jti=jti, expires_at=expires_at))
    db.commit()


def is_blacklisted(db: Session, jti: str) -> bool:
    return db.query(TokenBlacklist).filter(TokenBlacklist.jti == jti).first() is not None


def cleanup_blacklist(db: Session) -> None:
    """有効期限切れのブラックリストエントリを削除（定期実行推奨）。"""
    db.query(TokenBlacklist).filter(TokenBlacklist.expires_at < datetime.utcnow()).delete()
    db.commit()


# ── Company CRUD ─────────────────────────────────────────────────────────────

def get_company(db: Session, company_id: int):
    from auth.models import Company
    return db.query(Company).filter(Company.id == company_id).first()


def list_companies(db: Session):
    from auth.models import Company
    return db.query(Company).order_by(Company.created_at).all()


def create_company(db: Session, name: str, plan: str = "basic",
                   max_users: Optional[int] = None,
                   max_monthly_pages: Optional[int] = None,
                   contract_started_at=None):
    from auth.models import Company
    from core.config import PLAN_LIMITS
    if plan not in PLAN_LIMITS:
        raise ValueError(f"unknown plan: {plan}")
    defaults = PLAN_LIMITS[plan]
    c = Company(
        name=name,
        plan=plan,
        max_users=max_users if max_users is not None else defaults["max_users"],
        max_monthly_pages=max_monthly_pages if max_monthly_pages is not None else defaults["max_monthly_pages"],
        contract_started_at=contract_started_at,
        is_active=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def update_company(db: Session, company, **kwargs):
    from core.config import PLAN_LIMITS

    # plan change: if max_users / max_monthly_pages were NOT passed in this call,
    # the caller is intent on "switching plans" → auto-apply plan defaults.
    if "plan" in kwargs and kwargs["plan"] is not None:
        new_plan = kwargs["plan"]
        if new_plan not in PLAN_LIMITS:
            raise ValueError(f"unknown plan: {new_plan}")
        if "max_users" not in kwargs or kwargs.get("max_users") is None:
            kwargs["max_users"] = PLAN_LIMITS[new_plan]["max_users"]
        if "max_monthly_pages" not in kwargs or kwargs.get("max_monthly_pages") is None:
            kwargs["max_monthly_pages"] = PLAN_LIMITS[new_plan]["max_monthly_pages"]

    for key, value in kwargs.items():
        if value is not None:
            setattr(company, key, value)
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return company
