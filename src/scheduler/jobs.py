"""Scheduled jobs for scraping and email notifications."""

import asyncio
import logging

from src.db.database import SessionLocal
from src.scrapers.capitol_trades import scrape_capitol_trades
from src.scrapers.house import scrape_house_disclosures
from src.scrapers.senate import scrape_senate_disclosures
from src.scrapers.finnhub import scrape_finnhub_congress
from src.services.email_service import send_daily_notifications
from src.services.trade_service import ingest_trades

logger = logging.getLogger(__name__)


def run_scrape_job():
    """Scrape all sources and ingest new trades (daily sync — 25 pages Capitol Trades)."""
    logger.info("Starting scheduled scrape job (daily)")
    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()

        # Capitol Trades is the primary source — covers House + Senate with recent data
        capitol_trades = loop.run_until_complete(scrape_capitol_trades(max_pages=25))
        logger.info(f"Capitol Trades: {len(capitol_trades)} trades scraped")

        # House Clerk: filing metadata (no trade details without PDF parsing)
        house_trades = loop.run_until_complete(scrape_house_disclosures())
        logger.info(f"House Clerk: {len(house_trades)} filings scraped")

        # Senate GitHub: historical data only (repo stale since 2020)
        senate_trades = loop.run_until_complete(scrape_senate_disclosures())
        logger.info(f"Senate GitHub: {len(senate_trades)} trades scraped")

        # Finnhub: will bail early if API key is expired (403)
        finnhub_trades = loop.run_until_complete(scrape_finnhub_congress())
        logger.info(f"Finnhub: {len(finnhub_trades)} trades scraped")

        loop.close()

        all_trades = capitol_trades + senate_trades + house_trades + finnhub_trades
        new_count = ingest_trades(db, all_trades)
        logger.info(f"Scheduled scrape complete: {new_count} new trades ingested")
    except Exception as e:
        logger.error(f"Scrape job failed: {e}", exc_info=True)
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
