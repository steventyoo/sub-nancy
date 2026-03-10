"""Vercel serverless entry point — minimal test."""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()

@app.get("/")
def root():
    return PlainTextResponse("Nancy the Ripper is alive!")

@app.get("/{path:path}")
def catchall(path: str):
    return PlainTextResponse(f"Path: /{path}")
