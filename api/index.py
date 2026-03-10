"""Vercel serverless entry point for the FastAPI app."""

import sys
import os

# Ensure the project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import app

# Vercel expects the 'app' variable at module level
