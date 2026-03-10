"""Scheduled jobs for scraping and email notifications."""

import asyncio
import logging

from src.db.database import SessionLocal
from src.scrapers.house import scrape_house_disclosures
from src.scrapers.senate import scrape_senate_disclosures
from src.scrapers.finnhub import scrape_finnhub_congress
from src.services.email_service import send_daily_notifications
from src.services.trade_service import ingest_trades

logger = logging.getLogger(__name__)


def run_scrape_job():
    """Scrape all sources and ingest new trades."""
    logger.info("Starting scheduled scrape job")
    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()
        house_trades = loop.run_until_complete(scrape_house_disclosures())
        senate_trades = loop.run_until_complete(scrape_senate_disclosures())
        finnhub_trades = loop.run_until_complete(scrape_finnhub_congress())
        loop.close()

        all_trades = house_trades + senate_trades + finnhub_trades
        new_count = ingest_trades(db, all_trades)
        logger.info(f"Scheduled scrape complete: {new_count} new trades")
    except Exception as e:
        logger.error(f"Scrape job failed: {e}")
    finally:
        db.close()


def run_email_job():
    """Send daily email notifications."""
    logger.info("Starting scheduled email job")
    db = SessionLocal()
    try:
        send_daily_notifications(db)
    except Exception as e:
        logger.error(f"Email job failed: {e}")
    finally:
        db.close()
