"""
core/database.py
================
SQLAlchemy 数据库引擎、会话、基类。

支持 SQLite（本地开发）和 PostgreSQL（生产环境），
通过 DATABASE_URL 环境变量自动切换，代码零改动。

使用方式：
  from core.database import Base, get_db
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import settings

# SQLite 需要 check_same_thread=False（多线程 FastAPI）
_connect_args = (
    {"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    echo=settings.is_development,  # 开发环境打印 SQL，生产环境关闭
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖注入：提供数据库会话，请求结束自动关闭。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
