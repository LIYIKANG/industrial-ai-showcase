"""
auth/schemas.py
===============
Pydantic リクエスト/レスポンス スキーマ。
"""

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    allowed_pages: List[str]
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_encoders": {datetime: lambda v: v.replace(tzinfo=timezone.utc).isoformat()},
    }


class CompanyOut(BaseModel):
    id: int
    name: str
    plan: str
    max_users: int
    max_monthly_pages: int
    is_active: bool
    contract_started_at: Optional[datetime] = None
    subscription_status: str = "inactive"
    created_at: datetime
    model_config = {
        "from_attributes": True,
        "json_encoders": {datetime: lambda v: v.replace(tzinfo=timezone.utc).isoformat()},
    }


class CompanyCreate(BaseModel):
    name: str
    plan: str = "basic"
    max_users: Optional[int] = None       # if None, derived from plan
    max_monthly_pages: Optional[int] = None
    contract_started_at: Optional[datetime] = None


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    plan: Optional[str] = None
    max_users: Optional[int] = None
    max_monthly_pages: Optional[int] = None
    is_active: Optional[bool] = None
    contract_started_at: Optional[datetime] = None


class UserCreate(BaseModel):
    username: str
    display_name: str
    password: str
    role: str = "company_member"
    allowed_pages: Optional[List[str]] = None  # None = 全ページ許可
    company_id: Optional[int] = None  # super_admin が作成時に指定。company_owner 経由は呼出側で補完


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    allowed_pages: Optional[List[str]] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserTransferRequest(BaseModel):
    company_id: int
