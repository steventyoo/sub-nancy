import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Force load .env, overriding any empty env vars already set in shell
load_dotenv(_ENV_FILE, override=True)


class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///trades.db")
    email_from: str = os.getenv("EMAIL_FROM", "trades@yourdomain.com")
    scrape_interval_hours: int = int(os.getenv("SCRAPE_INTERVAL_HOURS", "24"))
    email_hour: int = int(os.getenv("EMAIL_HOUR", "8"))
    # Basic auth to lock down the app (set in Railway env vars)
    admin_user: str = os.getenv("ADMIN_USER", "")
    admin_pass: str = os.getenv("ADMIN_PASS", "")


settings = Settings()
