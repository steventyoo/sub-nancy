"""Scraper for Capitol Trades (capitoltrades.com).

Fetches recent congressional stock trades from both House and Senate.
Data is embedded as JSON in server-rendered Next.js pages.
"""

import json
import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com/trades"

# Capitol Trades amount ranges to numeric values
AMOUNT_MAP = {
    "$1K–$15K": (1001, 15000),
    "$15K–$50K": (15001, 50000),
    "$50K–$100K": (50001, 100000),
    "$100K–$250K": (100001, 250000),
    "$250K–$500K": (250001, 500000),
    "$500K–$1M": (500001, 1000000),
    "$1M–$5M": (1000001, 5000000),
    "$5M–$25M": (5000001, 25000000),
    "$25M–$50M": (25000001, 50000000),
    "$50M+": (50000001, None),
}


def parse_amount(value_str: str) -> tuple[float | None, float | None]:
    """Parse Capitol Trades amount range string to numeric low/high."""
    if not value_str:
        return None, None
    for label, (low, high) in AMOUNT_MAP.items():
        if label in value_str:
            return low, high
    # Try to parse as a direct range like "$1,001 - $15,000"
    nums = re.findall(r'[\d,]+', value_str)
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
    if o in ("self", "self"):
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


def extract_trades_from_html(html: str) -> list[dict]:
    """Extract trade data from Capitol Trades Next.js HTML page."""
    trades = []

    # Capitol Trades embeds data in Next.js script tags
    # Look for the JSON data in script tags
    patterns = [
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'"trades"\s*:\s*(\[.*?\])\s*[,}]',
    ]

    next_data = None
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                next_data = json.loads(match.group(1))
                break
            except json.JSONDecodeError:
                continue

    if not next_data:
        logger.warning("Could not find Next.js data in HTML")
        return trades

    # Navigate the Next.js data structure to find trades
    try:
        # Try common Next.js data paths
        page_props = next_data.get("props", {}).get("pageProps", {})
        seed_data = page_props.get("initialSeedData", page_props)

        # The trades might be in different locations
        trade_list = None
        if isinstance(seed_data, dict):
            trade_list = seed_data.get("trades", seed_data.get("data", []))
        elif isinstance(seed_data, list):
            trade_list = seed_data

        if not trade_list:
            # Try flattened structure
            for key in page_props:
                val = page_props[key]
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    if "txDate" in val[0] or "ticker" in str(val[0].keys()):
                        trade_list = val
                        break

        if not trade_list:
            logger.warning("Could not locate trade list in Next.js data")
            return trades

    except Exception as e:
        logger.error(f"Error navigating Next.js data: {e}")
        return trades

    for tx in trade_list:
        if not isinstance(tx, dict):
            continue

        try:
            # Extract politician info
            politician = tx.get("politician", {}) or {}
            first = politician.get("firstName", "") or ""
            last = politician.get("lastName", "") or ""
            member_name = f"{first} {last}".strip()

            # Extract issuer info
            issuer = tx.get("issuer", {}) or {}
            ticker = issuer.get("issuerTicker", "") or None
            asset_desc = issuer.get("issuerName", "") or ""

            # Parse dates
            tx_date = None
            if tx.get("txDate"):
                try:
                    tx_date = datetime.fromisoformat(
                        tx["txDate"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)
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
            amount_low, amount_high = parse_amount(tx.get("value", "") or "")

            # Map party
            party = politician.get("party", "")
            if party:
                party = party.title()

            chamber = normalize_chamber(
                tx.get("chamber", "") or politician.get("chamber", "")
            )

            trades.append({
                "member_name": member_name,
                "chamber": chamber,
                "party": party or None,
                "state": None,
                "district": None,
                "filing_date": pub_date,
                "ticker": ticker if ticker and ticker != "--" else None,
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
            })
        except Exception as e:
            logger.debug(f"Skipping trade entry: {e}")
            continue

    return trades


async def scrape_capitol_trades(
    max_pages: int = 30,
    page_size: int = 96,
) -> list[dict]:
    """Scrape recent trades from Capitol Trades.

    Args:
        max_pages: Maximum number of pages to fetch (96 trades per page).
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
            "Accept": "text/html,application/xhtml+xml",
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
