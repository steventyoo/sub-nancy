import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from src.config import settings

IS_VERCEL = os.environ.get("VERCEL", "") == "1"

# Use NullPool on Vercel (serverless) to avoid stale connections
engine_kwargs = {}
if "sqlite" in settings.database_url:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif IS_VERCEL:
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs["pool_pre_ping"] = True

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from src.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
