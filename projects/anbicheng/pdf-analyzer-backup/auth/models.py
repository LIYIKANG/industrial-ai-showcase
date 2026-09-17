"""
auth/models.py
==============
SQLAlchemy ORM モデル：ユーザー、監査ログ、トークンブラックリスト、ログイン試行。
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON

from core.database import Base

# アクセス制御対象のページ一覧
ALL_PAGES = ["customer", "jusetsu", "seisansho", "seikyusho", "beta"]


class Company(Base):
    """テナント（契約企業）。ユーザー・ジョブはこの会社に属する。"""

    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    plan = Column(String(20), nullable=False, default="basic")
    max_users = Column(Integer, nullable=False, default=2)
    # NAME is max_monthly_pages — we count by ProcessingJob.page_count, not by job count
    max_monthly_pages = Column(Integer, nullable=False, default=50)
    is_active = Column(Boolean, default=True, nullable=False)
    contract_started_at = Column(DateTime, nullable=True)
    # ── Stripe 決済（Phase 1: サブスク） ──
    stripe_customer_id = Column(String(64), nullable=True)       # Stripe Customer ID
    stripe_subscription_id = Column(String(64), nullable=True)   # 現行 Subscription ID
    # inactive / active / trialing / past_due / canceled
    subscription_status = Column(String(20), nullable=False, default="inactive")
    current_period_end = Column(DateTime, nullable=True)         # 請求期間終了（UTC naive）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="company_member")  # super_admin / company_owner / company_member
    is_active = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=True, nullable=False)
    allowed_pages = Column(JSON, nullable=False, default=list(ALL_PAGES))  # アクセス可能ページ
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, index=True)
    action = Column(String(50), nullable=False)  # login_success / login_failed / logout / ...
    ip_address = Column(String(50))
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TokenBlacklist(Base):
    """ログアウト済みの Refresh Token JTI を保持（有効期限切れ後に自動削除対象）。"""

    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(100), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProcessingJob(Base):
    """PDF処理ジョブの結果を永続化（再ダウンロード・履歴表示用）。"""

    __tablename__ = "processing_jobs"

    id            = Column(Integer, primary_key=True, index=True)
    file_id       = Column(String(50), unique=True, nullable=False, index=True)
    username      = Column(String(50), nullable=False, index=True)
    filename      = Column(String(255), nullable=False)
    fields_filled = Column(Integer, default=0)
    fields_total  = Column(Integer, default=0)
    page_count    = Column(Integer, default=0)
    extracted_json = Column(Text, nullable=False, default="[]")  # 抽出フィールドJSON
    source_type   = Column(String(10), default="standard")       # "standard" / "beta"
    status        = Column(String(20), nullable=False, default="done")  # pending/processing/done/failed
    error_msg     = Column(Text, nullable=True)
    finished_at   = Column(DateTime, nullable=True)
    company_id    = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)


class CustomerProduct(Base):
    """客户关键词业务处理使用的产品主数据。

    product_json 保存配置化字段，business_key_* 保存当前客户配置下的主键索引。
    """

    __tablename__ = "customer_products"

    id = Column(Integer, primary_key=True, index=True)
    business_key_id = Column(String(80), nullable=False, default="internal_sku", index=True)
    business_key_value = Column(String(160), nullable=False, index=True)
    product_json = Column(Text, nullable=False, default="{}")
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CustomerOperationLog(Base):
    """客户关键词处理操作日志。"""

    __tablename__ = "customer_operation_logs"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    job_id = Column(String(50), nullable=True, index=True)
    operation_type = Column(String(50), nullable=False)
    business_key_id = Column(String(80), nullable=True)
    business_key_value = Column(String(160), nullable=True, index=True)
    raw_input = Column(Text, nullable=False, default="")
    parsed_fields_json = Column(Text, nullable=False, default="{}")
    process_result_json = Column(Text, nullable=False, default="{}")
    old_value_json = Column(Text, nullable=True)
    new_value_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LoginAttempt(Base):
    """ブルートフォース対策：ログイン試行を記録。"""

    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, index=True)
    ip_address = Column(String(50))
    success = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
