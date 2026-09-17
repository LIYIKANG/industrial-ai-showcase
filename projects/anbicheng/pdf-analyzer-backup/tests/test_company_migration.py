"""
tests/test_company_migration.py
===============================
Smoke tests for PR1 multi-tenancy DB schema and migration script.

Uses an in-memory SQLite DB by monkeypatching core.database.engine and
core.database.SessionLocal before importing the migration module.
"""

import importlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def in_memory_db(monkeypatch):
    """Swap core.database.engine/SessionLocal for an in-memory SQLite instance.

    A single shared connection is used so all sessions see the same database
    (the default :memory: URL is per-connection).
    """
    import core.database as db_module

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # StaticPool keeps one connection so :memory: state persists.
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    # Import models AFTER patching so any module-level engine refs (none currently)
    # would resolve correctly; also create the schema on the test engine.
    from auth import models  # noqa: F401

    db_module.Base.metadata.create_all(bind=test_engine)

    yield {
        "engine": test_engine,
        "SessionLocal": TestSessionLocal,
        "db_module": db_module,
    }

    db_module.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def test_company_model_crud(in_memory_db):
    from auth.models import Company

    SessionLocal = in_memory_db["SessionLocal"]
    db = SessionLocal()
    try:
        company = Company(
            name="acme",
            plan="standard",
            max_users=3,
            max_monthly_pages=3000,
        )
        db.add(company)
        db.commit()
        db.refresh(company)
        assert company.id is not None

        loaded = db.query(Company).filter(Company.name == "acme").one()
        assert loaded.plan == "standard"
        assert loaded.max_users == 3
        assert loaded.is_active is True
        assert loaded.created_at is not None
    finally:
        db.close()


def _reload_migration_module():
    """Reload migrate_add_company so it picks up the patched engine/SessionLocal."""
    import scripts.migrate_add_company as mig

    return importlib.reload(mig)


def test_migration_creates_default_company_and_backfills(in_memory_db, monkeypatch):
    from auth.models import Company, ProcessingJob, User

    SessionLocal = in_memory_db["SessionLocal"]

    # Seed an existing user and job WITHOUT company_id.
    db = SessionLocal()
    try:
        db.add(
            User(
                username="legacy_user",
                display_name="Legacy",
                hashed_password="x",
                role="operator",
                allowed_pages=["jusetsu"],
            )
        )
        db.add(
            ProcessingJob(
                file_id="abc123",
                username="legacy_user",
                filename="x.pdf",
                page_count=10,
            )
        )
        db.commit()
    finally:
        db.close()

    mig = _reload_migration_module()
    mig.migrate()

    db = SessionLocal()
    try:
        default = db.query(Company).filter(Company.name == "default").one()
        assert default.plan == "internal"
        assert default.max_users == 9999

        user = db.query(User).filter(User.username == "legacy_user").one()
        assert user.company_id == default.id

        job = db.query(ProcessingJob).filter(ProcessingJob.file_id == "abc123").one()
        assert job.company_id == default.id
    finally:
        db.close()


def test_migration_is_idempotent(in_memory_db):
    from auth.models import Company, User

    SessionLocal = in_memory_db["SessionLocal"]

    db = SessionLocal()
    try:
        db.add(
            User(
                username="u1",
                display_name="U1",
                hashed_password="x",
                role="operator",
                allowed_pages=["jusetsu"],
            )
        )
        db.commit()
    finally:
        db.close()

    mig = _reload_migration_module()
    mig.migrate()
    mig.migrate()  # second run must be a no-op

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(Company.name == "default").all()
        assert len(companies) == 1  # not duplicated

        users = db.query(User).all()
        assert len(users) == 1
        assert users[0].company_id == companies[0].id
    finally:
        db.close()
