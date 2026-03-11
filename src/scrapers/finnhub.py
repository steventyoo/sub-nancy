"""Finnhub API scraper for congressional trading data (House + Senate).

Finnhub provides parsed, structured data for both chambers.
Free tier: 60 calls/minute. Congressional trading endpoint returns paginated results.

We paginate by fetching year-by-year chunks to ensure full coverage.
"""

import asyncio
import logging
from datetime import datetime

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"

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


def parse_amount(amount_from, amount_to) -> tuple[float | None, float | None]:
    """Parse Finnhub amount fields (they provide numeric values or range strings)."""
    if isinstance(amount_from, (int, float)) and amount_from > 0:
        return float(amount_from), float(amount_to) if amount_to else None

    # Sometimes returned as strings
    if isinstance(amount_from, str):
        for label, (low, high) in AMOUNT_RANGES.items():
            if label in amount_from:
                return low, high
    return None, None


def parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_entry(entry: dict) -> dict | None:
    """Parse a single Finnhub trade entry into our normalized format."""
    name = entry.get("name", "").strip()
    if not name:
        return None

    ticker = entry.get("symbol", "").strip() or None
    tx_type_raw = entry.get("transactionType", "") or ""
    tx_date = parse_date(entry.get("transactionDate", ""))
    filing_date = parse_date(entry.get("filingDate", ""))
    asset_desc = entry.get("assetName", "") or ""
    owner = entry.get("ownerType", "") or None
    position = entry.get("position", "") or ""

    # Determine chamber from position field
    chamber = "House"
    if "senator" in position.lower() or "senate" in position.lower():
        chamber = "Senate"
    elif "representative" in position.lower() or "rep." in position.lower():
        chamber = "House"

    # Normalize transaction type
    tx_type = tx_type_raw
    if tx_type:
        tx_lower = tx_type.lower()
        if "purchase" in tx_lower or "buy" in tx_lower:
            tx_type = "Purchase"
        elif "sale" in tx_lower:
            if "full" in tx_lower:
                tx_type = "Sale (Full)"
            elif "partial" in tx_lower:
                tx_type = "Sale (Partial)"
            else:
                tx_type = "Sale"
        elif "exchange" in tx_lower:
            tx_type = "Exchange"

    amount_low, amount_high = parse_amount(
        entry.get("amountFrom"), entry.get("amountTo")
    )

    return {
        "member_name": name,
        "chamber": chamber,
        "state": None,
        "district": None,
        "filing_date": filing_date,
        "ticker": ticker,
        "asset_description": asset_desc,
        "asset_type": None,
        "transaction_type": tx_type or None,
        "transaction_date": tx_date,
        "amount_low": amount_low,
        "amount_high": amount_high,
        "owner": owner,
        "raw_filing_url": None,
        "source": "finnhub",
    }


async def scrape_finnhub_congress(
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch congressional trading data from Finnhub API.

    Paginates by fetching 6-month chunks to ensure we don't hit
    response size limits on the free tier.

    Args:
        from_date: Start date YYYY-MM-DD (default: 2020-01-01)
        to_date: End date YYYY-MM-DD (default: today)
    """
    if not settings.finnhub_api_key:
        logger.warning("No Finnhub API key configured, skipping Finnhub scrape")
        return []

    if from_date is None:
        from_date = "2020-01-01"
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")

    # Build 6-month date chunks for pagination
    from datetime import timedelta

    chunks = []
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end = datetime.strptime(to_date, "%Y-%m-%d")
    while start < end:
        chunk_end = min(start + timedelta(days=180), end)
        chunks.append((start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        start = chunk_end + timedelta(days=1)

    all_trades = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for chunk_from, chunk_to in chunks:
            params = {
                "token": settings.finnhub_api_key,
                "from": chunk_from,
                "to": chunk_to,
            }

            try:
                resp = await client.get(
                    f"{BASE_URL}/stock/congressional-trading", params=params
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Finnhub API error for {chunk_from}-{chunk_to}: {e}")
                await asyncio.sleep(2)
                continue

            data = resp.json()

            if not isinstance(data, list):
                data = data.get("data", [])

            chunk_trades = []
            for entry in data:
                trade = _parse_entry(entry)
                if trade:
                    chunk_trades.append(trade)

            all_trades.extend(chunk_trades)
            logger.info(
                f"Finnhub {chunk_from} to {chunk_to}: {len(chunk_trades)} trades "
                f"(total: {len(all_trades)})"
            )

            # Rate limit: Finnhub free tier is 60 calls/min
            await asyncio.sleep(1.1)

    logger.info(f"Total Finnhub trades fetched: {len(all_trades)}")
    return all_trades
