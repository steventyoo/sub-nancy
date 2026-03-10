"""Scraper for Senate financial disclosures.

Primary: Pull pre-scraped JSON from the senate-stock-watcher-data GitHub repo.
This is far more reliable than scraping efdsearch.senate.gov directly.
"""

import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master"
)

AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$15,001 - $50,000": (15001, 50000),
    "$50,001 - $100,000": (50001, 100000),
    "$100,001 - $250,000": (100001, 250000),
    "$250,001 - $500,000": (250001, 500000),
    "$500,001 - $1,000,000": (500001, 1000000),
    "$1,000,001 - $5,000,000": (1000001, 5000000),
    "$5,000,001 - $25,000,000": (5000001, 25000000),
    "$25,000,001 - $50,000,000": (25000001, 50000000),
    "Over $50,000,000": (50000001, None),
}


def parse_amount(amount_str: str) -> tuple[float | None, float | None]:
    if not amount_str:
        return None, None
    for label, (low, high) in AMOUNT_RANGES.items():
        if label in amount_str:
            return low, high
    return None, None


def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


async def scrape_senate_disclosures() -> list[dict]:
    """Pull all Senate transactions from the GitHub data repo."""
    url = f"{GITHUB_RAW_BASE}/aggregate/all_transactions.json"
    trades = []

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch Senate data from GitHub: {e}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to parse Senate JSON: {e}")
            return []

    for entry in data:
        tx = entry.get("transaction", entry)

        # Handle both flat and nested formats
        if isinstance(tx, dict):
            ticker = tx.get("ticker", "").strip() or None
            asset_desc = tx.get("asset_description", "") or tx.get("asset_name", "") or ""
            tx_type = tx.get("type", "") or tx.get("transaction_type", "")
            tx_date = parse_date(tx.get("transaction_date", ""))
            amount_str = tx.get("amount", "") or ""
            owner = tx.get("owner", "")
            asset_type = tx.get("asset_type", "")
        else:
            continue

        amount_low, amount_high = parse_amount(amount_str)

        # Normalize transaction type
        if tx_type:
            tx_type_lower = tx_type.lower().strip()
            if "purchase" in tx_type_lower:
                tx_type = "Purchase"
            elif "sale" in tx_type_lower:
                if "full" in tx_type_lower:
                    tx_type = "Sale (Full)"
                elif "partial" in tx_type_lower:
                    tx_type = "Sale (Partial)"
                else:
                    tx_type = "Sale"
            elif "exchange" in tx_type_lower:
                tx_type = "Exchange"

        # Skip non-stock entries (bonds, options, etc.) if no ticker
        if ticker and ticker == "--":
            ticker = None

        member_name = entry.get("senator", "") or entry.get("full_name", "") or ""
        filing_date = parse_date(entry.get("disclosure_date", "") or entry.get("filing_date", ""))
        ptr_link = entry.get("ptr_link", "") or entry.get("source_url", "")

        trades.append({
            "member_name": member_name.strip(),
            "chamber": "Senate",
            "state": None,  # Not in this dataset
            "district": None,
            "filing_date": filing_date,
            "ticker": ticker,
            "asset_description": asset_desc,
            "asset_type": asset_type or None,
            "transaction_type": tx_type or None,
            "transaction_date": tx_date,
            "amount_low": amount_low,
            "amount_high": amount_high,
            "owner": owner or None,
            "raw_filing_url": ptr_link or None,
            "source": "senate_github",
        })

    logger.info(f"Fetched {len(trades)} Senate transactions from GitHub")
    return trades
