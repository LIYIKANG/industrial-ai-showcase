"""
core/config.py
==============
全局配置：多环境支持、路径常量、字段映射加载、Anthropic 客户端单例。

环境切换：通过 APP_ENV 环境变量控制（development / production）
  - development：加载 .env（本地开发默认）
  - production ：加载 .env.production（服务器端手动创建，不提交 git）

所有其他模块从这里 import settings、BASE_DIR、load_config()、claude_client。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

import anthropic
from dotenv import load_dotenv

# ── 路径常量 ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # 项目根目录

# ── 加载环境变量 ───────────────────────────────────────────────────────────────
# 先读系统环境变量中的 APP_ENV，再决定加载哪个 .env 文件
_APP_ENV = os.getenv("APP_ENV", "development")
_env_file = BASE_DIR / (".env" if _APP_ENV == "development" else f".env.{_APP_ENV}")
load_dotenv(_env_file, override=True)


# ── Settings：统一配置入口 ────────────────────────────────────────────────────
class Settings:
    """从环境变量读取所有配置项，提供类型转换和默认值。

    使用方式：from core.config import settings
    """

    # 运行环境
    APP_ENV: str = os.getenv("APP_ENV", "development")

    # 服务器
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "9000"))

    # Claude AI
    CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

    # CORS（多个 origin 用英文逗号分隔，生产环境应限制为具体域名）
    CORS_ORIGINS: list = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
    ]

    # 文件存储（相对于 BASE_DIR 或绝对路径均可）
    OUTPUT_DIR: Path = BASE_DIR / os.getenv("OUTPUT_DIR", "output")

    # ── 客户关键词系统 ────────────────────────────────────────────────────────
    CUSTOMER_KEYWORD_CONFIG_PATH: Path = BASE_DIR / os.getenv(
        "CUSTOMER_KEYWORD_CONFIG_PATH",
        "config/customer_keywords.json",
    )
    CUSTOMER_PRODUCT_SEED_FILE: Path = BASE_DIR / os.getenv(
        "CUSTOMER_PRODUCT_SEED_FILE",
        "data/customer_product_database_seed.json",
    )
    CUSTOMER_EXPORT_DIR: Path = BASE_DIR / os.getenv(
        "CUSTOMER_EXPORT_DIR",
        "output/customer_exports",
    )
    CUSTOMER_MAX_UPLOAD_FILES: int = int(os.getenv("CUSTOMER_MAX_UPLOAD_FILES", "20"))
    CUSTOMER_MAX_CONCURRENT_JOBS: int = int(os.getenv("CUSTOMER_MAX_CONCURRENT_JOBS", "2"))
    CUSTOMER_POLL_INTERVAL_SECONDS: int = int(os.getenv("CUSTOMER_POLL_INTERVAL_SECONDS", "2"))

    # 日志级别
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # 上传文件大小限制（MB）
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))

    # ── Beta ページ：テンプレートアップロード ────────────────────────────────────
    # ユーザーがアップロードした出力テンプレートの保存先
    TEMPLATE_UPLOAD_DIR: Path = BASE_DIR / os.getenv("TEMPLATE_UPLOAD_DIR", "data/user_templates")
    # テンプレートファイルの最大サイズ（MB）
    MAX_TEMPLATE_SIZE_MB: int = int(os.getenv("MAX_TEMPLATE_SIZE_MB", "10"))

    # ── データベース ───────────────────────────────────────────────────────────
    # SQLite（ローカル開発）または PostgreSQL（本番）を DATABASE_URL で切り替え
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'data' / 'app.db'}",
    )

    # ── JWT 認証 ──────────────────────────────────────────────────────────────
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "15"))
    JWT_REFRESH_EXPIRE_DAYS: int = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))

    # ── ログイン制限 ──────────────────────────────────────────────────────────
    MAX_LOGIN_ATTEMPTS: int = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES: int = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

    # ── Stripe 決済 ────────────────────────────────────────────────────────────
    # 未設定でもアプリは起動可能。billing エンドポイント利用時にのみ必須。
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    # Checkout の success/cancel・Portal return 先のベース URL（末尾スラッシュ無し）
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:9000").rstrip("/")

    @property
    def is_stripe_configured(self) -> bool:
        return bool(self.STRIPE_SECRET_KEY)

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    def validate(self) -> None:
        """启动时校验必填配置，缺失则直接报错。"""
        if not self.CLAUDE_API_KEY:
            raise RuntimeError(
                f"未找到 CLAUDE_API_KEY 环境变量，请在 {_env_file.name} 中设置。"
            )
        if not self.JWT_SECRET:
            raise RuntimeError(
                f"未找到 JWT_SECRET 环境变量，请在 {_env_file.name} 中设置。\n"
                "生成方法：python -c \"import secrets; print(secrets.token_hex(32))\""
            )


settings = Settings()
settings.validate()

# ── プラン別利用上限 ───────────────────────────────────────────────────────────
# 注: 価格表は「PDF枚数/月」で表示しているが、内部はページ数で集計する。
# 1 PDF ≒ 30 ページ平均の前提で換算（pitch deck より）。
PLAN_LIMITS = {
    "basic":     {"max_users": 2, "max_monthly_pages": 50 * 30},      # PDF50枚 ≒ avg 30p/枚
    "standard":  {"max_users": 3, "max_monthly_pages": 100 * 30},
    "pro":       {"max_users": 4, "max_monthly_pages": 200 * 30},
    "flagship":  {"max_users": 5, "max_monthly_pages": 400 * 30},
    "internal":  {"max_users": 9999, "max_monthly_pages": 99999999},  # for system default company
}

# ── Stripe Price ↔ プラン マッピング ───────────────────────────────────────────
# 課金対象プランのみ（internal は社内用で課金しない）。
# Stripe ダッシュボードで作成した Price ID を env（STRIPE_PRICE_BASIC 等）で渡す。
BILLABLE_PLANS = ["basic", "standard", "pro", "flagship"]
PLAN_PRICE_IDS = {
    plan: os.getenv(f"STRIPE_PRICE_{plan.upper()}", "")
    for plan in BILLABLE_PLANS
}
# 逆引き（Webhook で price→plan を解決）。未設定（空文字）の plan は除外。
PRICE_ID_TO_PLAN = {
    price_id: plan
    for plan, price_id in PLAN_PRICE_IDS.items()
    if price_id
}

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.info("env=%s model=%s log_level=%s", settings.APP_ENV, settings.CLAUDE_MODEL, settings.LOG_LEVEL)

# ── 路径 ──────────────────────────────────────────────────────────────────────
CONFIG_PATH = BASE_DIR / "config" / "field_mapping.json"
SEISANSHO_CONFIG_PATH = BASE_DIR / "config" / "seisansho_field_mapping.json"
CUSTOMER_KEYWORD_CONFIG_PATH = settings.CUSTOMER_KEYWORD_CONFIG_PATH

# 确保输出目录存在
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMPLATE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.CUSTOMER_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


# ── 字段映射配置 ───────────────────────────────────────────────────────────────
def load_config(config_path: Path = None) -> Dict[str, Any]:
    """读取并返回字段映射配置（每次请求时调用，支持热更新）。"""
    path = config_path or CONFIG_PATH
    if not path.exists():
        raise RuntimeError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_customer_keyword_config(config_id: str | None = None) -> Dict[str, Any]:
    """读取客户关键词配置，支持按配置 id 或文件名选择。"""
    base_path = settings.CUSTOMER_KEYWORD_CONFIG_PATH
    candidates = [base_path, *sorted(base_path.parent.glob("*.json"))]

    seen = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        with path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        if config_id is None or cfg.get("id") == config_id or path.stem == config_id:
            return cfg

    raise RuntimeError(f"客户关键词配置不存在: {config_id or base_path}")


def _is_customer_keyword_config(cfg: Dict[str, Any]) -> bool:
    fields = cfg.get("fields")
    if cfg.get("type") == "customer_keywords":
        return isinstance(fields, list)
    if not isinstance(fields, list) or not fields:
        return False
    return any(
        isinstance(field, dict)
        and "aliases" in field
        and "parser_type" in field
        and "input_position" in field
        for field in fields
    )


def list_customer_keyword_configs() -> list[Dict[str, Any]]:
    """列出可选客户关键词配置，供前端下拉选择。"""
    base_path = settings.CUSTOMER_KEYWORD_CONFIG_PATH
    configs: list[Dict[str, Any]] = []
    seen_ids = set()

    for path in sorted(base_path.parent.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        cfg_id = cfg.get("id") or path.stem
        if not _is_customer_keyword_config(cfg) or cfg_id in seen_ids:
            continue
        seen_ids.add(cfg_id)
        configs.append(
            {
                "id": cfg_id,
                "label": cfg.get("label", cfg_id),
                "description": cfg.get("description", ""),
                "fields_count": len(cfg.get("fields", [])),
                "is_default": path.resolve() == base_path.resolve(),
            }
        )
    return configs


# ── Anthropic 客户端单例 ───────────────────────────────────────────────────────
claude_client = anthropic.AsyncAnthropic(api_key=settings.CLAUDE_API_KEY)

# ── 向后兼容：保留旧的模块级变量供其他模块直接 import ────────────────────────────
CLAUDE_MODEL: str = settings.CLAUDE_MODEL
