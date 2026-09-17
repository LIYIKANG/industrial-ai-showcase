"""
app.py
======
应用入口：FastAPI 实例创建、中间件、静态文件挂载、路由注册。

业务逻辑已拆分到：
  core/config.py       - 配置与 Anthropic 客户端
  core/pdf_utils.py    - PDF 处理工具
  core/ai_extractor.py - AI 字段提取
  core/excel_writer.py - Excel 模板填写
  api/routes.py        - 所有 HTTP 路由
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.beta_routes import beta_router
from api.billing_routes import billing_router
from api.customer_routes import customer_router
from api.routes import router
from api.seisansho_routes import seisansho_router
from api.seikyusho_routes import seikyusho_router
from auth.models import ProcessingJob
from auth.router import router as auth_router
from auth.service import cleanup_blacklist
from core.config import BASE_DIR, settings
from core.database import SessionLocal

# アプリ全体のログ出力設定（Render の Logs に INFO 以上が流れる）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時: 前回サーバー再起動で中断した processing ジョブを failed に戻す
    db = SessionLocal()
    try:
        stale = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.status.in_(["pending", "queued", "processing"]))
            .all()
        )
        for job in stale:
            job.status = "failed"
            job.error_msg = "サーバー再起動により中断"
        if stale:
            db.commit()
        # 期限切れ Refresh Token ブラックリストを清掃（テーブル肥大防止）
        cleanup_blacklist(db)
    finally:
        db.close()
    yield


# ── FastAPI 应用 ──────────────────────────────────────────────────────────────

app = FastAPI(title="Customer Keyword Analyzer", version="2.0.0", lifespan=lifespan)

# 允许跨域（来源由 CORS_ORIGINS 环境变量控制，生产环境应限制为具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# 路由
app.include_router(auth_router)  # 認証・ユーザー管理（/login, /auth/*, /admin）
app.include_router(router)       # 既存機能（/, /api/*）
app.include_router(customer_router)  # 客户关键词识别（/api/customer/*）
app.include_router(seisansho_router)   # 精算書（/api/seisansho/*）
app.include_router(seikyusho_router)   # 請求書（/api/seikyusho/*）
app.include_router(beta_router)        # Beta ページ（/beta, /api/beta/*）
app.include_router(billing_router)     # Stripe 決済（/api/billing/*）


# ── 启动入口（本地开发用，生产环境由 Gunicorn 启动） ──────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.is_development,
    )
