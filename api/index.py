"""Vercel serverless entry point for the FastAPI app."""

import sys
import os
import traceback

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from src.main import app
except Exception as e:
    # If import fails, create a minimal app that shows the error
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()

    error_msg = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"

    @app.get("/{path:path}")
    def error_handler(path: str = ""):
        return PlainTextResponse(error_msg, status_code=500)
