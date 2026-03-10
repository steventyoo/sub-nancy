"""Vercel serverless entry point."""

import sys
import os

# Ensure project root is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test: just try importing each dep to find the problematic one
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()

errors = []
for mod_name in ['sqlalchemy', 'httpx', 'bs4', 'anthropic', 'resend',
                 'apscheduler', 'pydantic', 'dotenv', 'lxml', 'psycopg2']:
    try:
        __import__(mod_name)
        errors.append(f"OK: {mod_name}")
    except Exception as e:
        errors.append(f"FAIL: {mod_name} -> {e}")

@app.get("/{path:path}")
def root(path: str = ""):
    return PlainTextResponse("\n".join(errors))
