"""
auth/dependencies.py
====================
FastAPI 依赖注入：从 Authorization ヘッダーでトークンを検証し
現在のユーザーを返す。ルート保護の基盤。
"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from auth.models import User
from auth.service import decode_token, get_user, is_blacklisted
from core.database import get_db

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Access Token を検証して User を返す。無効なら 401。"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無効なトークンです。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    if jti and is_blacklisted(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="トークンは無効化されています。再度ログインしてください。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload.get("sub")
    user = get_user(db, username) if username else None
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザーが見つかりません。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    """システム管理者（super_admin / root_admin）ロールを要求する。

    root_admin は super_admin の権限を包含するため、super_admin 用ゲートを通過する。
    """
    if current_user.role not in ("super_admin", "root_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="システム管理者権限が必要です。",
        )
    return current_user


def require_root_admin(current_user: User = Depends(get_current_user)) -> User:
    """最上位管理者（root_admin）ロールを要求する。"""
    if current_user.role != "root_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="root 管理者権限が必要です。",
        )
    return current_user


def require_company_admin(current_user: User = Depends(get_current_user)) -> User:
    """root_admin / super_admin / company_owner — admin スコープ管理者。"""
    if current_user.role not in ("root_admin", "super_admin", "company_owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理者権限が必要です。",
        )
    return current_user
