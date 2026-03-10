"""Vercel serverless entry point."""

import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# Step-by-step import to find the failure
steps = []
try:
    steps.append("1. importing config...")
    from src.config import settings
    steps.append(f"   OK (db_url starts with: {settings.database_url[:30]})")

    steps.append("2. importing database...")
    from src.db.database import init_db, engine
    steps.append("   OK")

    steps.append("3. testing db connection...")
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    steps.append("   OK - connected to database")

    steps.append("4. importing main app...")
    from src.main import app
    steps.append("   OK - app imported!")

except Exception as e:
    steps.append(f"   FAILED: {traceback.format_exc()}")
    app = FastAPI()

    @app.get("/{path:path}")
    def debug(path: str = ""):
        return PlainTextResponse("\n".join(steps))
