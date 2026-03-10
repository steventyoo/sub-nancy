"""Scraper for Capitol Trades (capitoltrades.com).

Fetches recent congressional stock trades from both House and Senate.
Data is embedded as JSON in React Server Component (RSC) flight data
within the Next.js App Router HTML response.
"""

import json
import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com/trades"

# Capitol Trades "value" field is now a numeric dollar amount.
# We map it to amount_low/amount_high range buckets for consistency.
AMOUNT_BUCKETS = [
    (1001, 15000),
    (15001, 50000),
    (50001, 100000),
    (100001, 250000),
    (250001, 500000),
    (500001, 1000000),
    (1000001, 5000000),
    (5000001, 25000000),
    (25000001, 50000000),
]


def parse_amount(value) -> tuple[float | None, float | None]:
    """Parse Capitol Trades value to amount_low/amount_high.

    The new API returns a numeric dollar value (e.g., 8000).
    We bucket it into our standard ranges.
    """
    if value is None:
        return None, None

    # Handle numeric values (new format)
    if isinstance(value, (int, float)):
        v = float(value)
        for low, high in AMOUNT_BUCKETS:
            if v <= high:
                return float(low), float(high)
        # Above $50M
        return 50000001.0, None

    # Handle string values (legacy format)
    if isinstance(value, str):
        nums = re.findall(r'[\d,]+', value)
        if len(nums) >= 2:
            try:
                return float(nums[0].replace(",", "")), float(nums[1].replace(",", ""))
            except ValueError:
                pass
        elif len(nums) == 1:
            try:
                v = float(nums[0].replace(",", ""))
                return v, v
            except ValueError:
                pass

    return None, None


def normalize_tx_type(tx_type: str) -> str:
    """Normalize Capitol Trades transaction type to our standard format."""
    if not tx_type:
        return None
    t = tx_type.lower().strip()
    if t == "buy" or "purchase" in t:
        return "Purchase"
    elif t == "sell" or "sale" in t:
        if "full" in t:
            return "Sale (Full)"
        elif "partial" in t:
            return "Sale (Partial)"
        return "Sale"
    elif "exchange" in t:
        return "Exchange"
    return tx_type


def normalize_owner(owner: str) -> str | None:
    """Normalize owner field."""
    if not owner:
        return None
    o = owner.lower().strip()
    if o in ("self",):
        return "Self"
    elif "spouse" in o:
        return "Spouse"
    elif "child" in o:
        return "Child"
    elif "joint" in o:
        return "Joint"
    elif "not" in o and "disclosed" in o:
        return None
    return owner.title()


def normalize_chamber(chamber: str) -> str:
    """Normalize chamber to our format."""
    if not chamber:
        return "Senate"
    c = chamber.lower().strip()
    if c == "house":
        return "House"
    return "Senate"


def clean_ticker(ticker_str: str | None) -> str | None:
    """Clean ticker symbol — remove exchange suffix like ':US'."""
    if not ticker_str or ticker_str == "--":
        return None
    # Remove exchange suffix (e.g., "GOOGL:US" -> "GOOGL")
    return ticker_str.split(":")[0].strip() or None


def extract_trades_from_html(html: str) -> list[dict]:
    """Extract trade data from Capitol Trades HTML.

    Capitol Trades uses Next.js App Router with React Server Components.
    Trade data is embedded in RSC flight data within self.__next_f.push() calls.
    """
    trades = []

    # Strategy 1: Extract from RSC flight data (current format)
    # The data is in self.__next_f.push([1,"..."]) calls with escaped JSON
    try:
        # Find RSC chunk containing trade data (_txId is unique to trade objects)
        match = re.search(
            r'self\.__next_f\.push\(\[1,"(.*?_txId.*?)"\]\)', html, re.DOTALL
        )
        if match:
            chunk = match.group(1)
            # Unescape RSC string encoding
            chunk = chunk.replace('\\"', '"').replace('\\n', '\n')

            # Find JSON array of trade objects
            arr_match = re.search(
                r'\[(\{"_issuerId.*?"value":\d+\}'
                r'(?:,\{"_issuerId.*?"value":\d+\})*)\]',
                chunk,
                re.DOTALL,
            )
            if arr_match:
                trade_list = json.loads(arr_match.group(0))
                logger.info(f"Extracted {len(trade_list)} trades from RSC data")

                for tx in trade_list:
                    trade = _parse_trade(tx)
                    if trade:
                        trades.append(trade)
                return trades
    except Exception as e:
        logger.error(f"Error extracting RSC data: {e}")

    # Strategy 2: Try legacy __NEXT_DATA__ format (fallback)
    try:
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if match:
            next_data = json.loads(match.group(1))
            page_props = next_data.get("props", {}).get("pageProps", {})
            seed_data = page_props.get("initialSeedData", page_props)

            trade_list = None
            if isinstance(seed_data, dict):
                trade_list = seed_data.get("trades", seed_data.get("data", []))
            elif isinstance(seed_data, list):
                trade_list = seed_data

            if trade_list:
                for tx in trade_list:
                    trade = _parse_trade(tx)
                    if trade:
                        trades.append(trade)
                return trades
    except Exception as e:
        logger.debug(f"Legacy __NEXT_DATA__ extraction failed: {e}")

    logger.warning("Could not extract trade data from Capitol Trades HTML")
    return trades


def _parse_trade(tx: dict) -> dict | None:
    """Parse a single trade object from Capitol Trades into our format."""
    if not isinstance(tx, dict):
        return None

    try:
        # Extract politician info
        politician = tx.get("politician", {}) or {}
        first = politician.get("firstName", "") or ""
        last = politician.get("lastName", "") or ""
        member_name = f"{first} {last}".strip()

        if not member_name:
            return None

        # Extract issuer info
        issuer = tx.get("issuer", {}) or {}
        raw_ticker = issuer.get("issuerTicker", "") or None
        ticker = clean_ticker(raw_ticker)
        asset_desc = issuer.get("issuerName", "") or ""

        # Parse dates
        tx_date = None
        if tx.get("txDate"):
            try:
                tx_date = datetime.fromisoformat(
                    tx["txDate"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, AttributeError):
                # Try simple date format "2026-02-26"
                try:
                    tx_date = datetime.strptime(tx["txDate"], "%Y-%m-%d")
                except (ValueError, AttributeError):
                    pass

        pub_date = None
        if tx.get("pubDate"):
            try:
                pub_date = datetime.fromisoformat(
                    tx["pubDate"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass

        # Parse amount
        amount_low, amount_high = parse_amount(tx.get("value"))

        # Map party
        party = politician.get("party", "")
        if party:
            party = party.title()

        chamber = normalize_chamber(
            tx.get("chamber", "") or politician.get("chamber", "")
        )

        # Map state from politician
        state = (politician.get("_stateId", "") or "").upper() or None

        return {
            "member_name": member_name,
            "chamber": chamber,
            "party": party or None,
            "state": state,
            "district": None,
            "filing_date": pub_date,
            "ticker": ticker,
            "asset_description": asset_desc,
            "asset_type": issuer.get("sector", None),
            "transaction_type": normalize_tx_type(
                tx.get("txType", "") or tx.get("txTypeExtended", "")
            ),
            "transaction_date": tx_date,
            "amount_low": amount_low,
            "amount_high": amount_high,
            "owner": normalize_owner(tx.get("owner", "")),
            "raw_filing_url": None,
            "source": "capitol_trades",
        }
    except Exception as e:
        logger.debug(f"Skipping trade entry: {e}")
        return None


async def scrape_capitol_trades(
    max_pages: int = 30,
    page_size: int = 96,
) -> list[dict]:
    """Scrape recent trades from Capitol Trades.

    Args:
        max_pages: Maximum number of pages to fetch.
        page_size: Number of trades per page (max 96).

    Returns:
        List of trade dicts compatible with ingest_trades().
    """
    all_trades = []

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}?page={page}&pageSize={page_size}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch Capitol Trades page {page}: {e}")
                break

            page_trades = extract_trades_from_html(resp.text)

            if not page_trades:
                logger.info(f"No more trades found at page {page}, stopping")
                break

            all_trades.extend(page_trades)
            logger.info(
                f"Page {page}: fetched {len(page_trades)} trades "
                f"(total: {len(all_trades)})"
            )

            # If we got fewer than expected, we've reached the end
            if len(page_trades) < page_size:
                break

    logger.info(f"Total Capitol Trades scraped: {len(all_trades)}")
    return all_trades
