"""Vercel serverless entry point for the FastAPI app."""

import sys
import os
import traceback

# Ensure project root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from src.main import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    _err = traceback.format_exc()
    app = FastAPI()

    @app.get("/{path:path}")
    def error_page(path: str = ""):
        return PlainTextResponse(f"Import error:\n\n{_err}", status_code=500)
