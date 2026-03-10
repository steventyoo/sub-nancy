"""Vercel serverless entry point."""

import sys
import os
import traceback

# Ensure project root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

_error = None
try:
    from src.main import app
except Exception as e:
    _error = traceback.format_exc()
    app = FastAPI()

    @app.get("/{path:path}")
    def error_page(path: str = ""):
        return PlainTextResponse(f"Import error:\n\n{_error}", status_code=500)
