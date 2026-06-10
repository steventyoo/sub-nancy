"""Unusual Whales API client (api.unusualwhales.com).

This replaces the public-page scraper (unusual_whales.py) with authenticated
API calls. Requires UW_API_TOKEN in the environment.

Why API over scraping:
  - No pagination cap / no HTML-format fragility
  - Per-politician and per-ticker querying via real params
  - Access to anomaly-tagged "unusual trades" and STOCK Act late-report
    endpoints that are NOT exposed on the public web page

Auth: every request sends `Authorization: Bearer <UW_API_TOKEN>`.
Rate limit: 120 req/min on all paid tiers — we sleep between calls.
"""

import asyncio
import logging
import os
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.unusualwhales.com/api"

AMOUNT_RANGES = {
    "$1,001 - $15,000": (1001, 15000),
    "$1,000 - $15,000": (1001, 15000),
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


def _token() -> str | None:
    return os.environ.get("UW_API_TOKEN", "").strip() or None


def _parse_amount(s: str | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    s = s.strip()
    for label, (low, high) in AMOUNT_RANGES.items():
        if label in s:
            return float(low), (float(high) if high else None)
    nums = re.findall(r"[\d,]+", s.replace("$", ""))
    if len(nums) >= 2:
        try:
            return float(nums[0].replace(",", "")), float(nums[1].replace(",", ""))
        except ValueError:
            pass
    return None, None


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _norm_tx(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower().strip()
    if "buy" in t or "purchase" in t:
        return "Purchase"
    if "sell" in t or "sale" in t:
        if "full" in t:
            return "Sale (Full)"
        if "partial" in t:
            return "Sale (Partial)"
        return "Sale"
    if "exchange" in t:
        return "Exchange"
    return s


def _norm_owner(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower().strip()
    if t in ("self", "undisclosed", "not-disclosed"):
        return "Self" if t == "self" else None
    if "spouse" in t:
        return "Spouse"
    if "joint" in t:
        return "Joint"
    if "child" in t or "dependent" in t:
        return "Child"
    return s.title()


def _norm_chamber(s: str | None) -> str:
    if not s:
        return "Senate"
    return "House" if s.lower().strip() == "house" else "Senate"


def _parse_trade(tx: dict) -> dict | None:
    """Convert one UW API trade object into our internal ingest format."""
    try:
        name = (tx.get("name") or tx.get("reporter") or "").strip()
        if not name:
            return None

        ticker = (tx.get("ticker") or "").strip() or None
        if ticker and (ticker.lower() in ("not-disclosed", "n/a", "--") or len(ticker) > 10):
            ticker = None

        amount_low, amount_high = _parse_amount(tx.get("amounts"))
        district = tx.get("current_district") or ""
        state = district.split("-")[0].upper() if district else None

        return {
            "member_name": name,
            "chamber": _norm_chamber(tx.get("current_chamber") or tx.get("member_type")),
            "party": (tx.get("current_party") or "").title() or None,
            "state": state,
            "district": district or None,
            "filing_date": _parse_date(tx.get("filed_at_date")),
            "ticker": ticker,
            "asset_description": tx.get("issuer") or tx.get("notes") or "",
            "asset_type": tx.get("asset"),
            "transaction_type": _norm_tx(tx.get("txn_type")),
            "transaction_date": _parse_date(tx.get("transaction_date")),
            "amount_low": amount_low,
            "amount_high": amount_high,
            "owner": _norm_owner(tx.get("affiliation") or tx.get("ownership")),
            "raw_filing_url": tx.get("link_url"),
            "source": "uw_api",
            # Anomaly metadata (only present on unusual-trades endpoint)
            "unusual_tags": tx.get("tags") or tx.get("unusual_types"),
        }
    except Exception as e:
        logger.debug(f"UW API trade parse error: {e}")
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict | None:
    """GET an API path with auth. Returns parsed JSON dict or None."""
    token = _token()
    if not token:
        logger.error("UW_API_TOKEN not set — cannot call UW API")
        return None
    try:
        r = await client.get(
            f"{API_BASE}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if r.status_code == 401:
            logger.error("UW API 401 — token invalid or expired")
            return None
        if r.status_code == 429:
            logger.warning("UW API 429 — rate limited, backing off 5s")
            await asyncio.sleep(5)
            return await _get(client, path, params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        logger.error(f"UW API GET {path} failed: {e}")
        return None
    except ValueError as e:
        logger.error(f"UW API GET {path} non-JSON: {e}")
        return None


async def scrape_uw_api(max_pages: int = 20, page_size: int = 200) -> list[dict]:
    """Pull recent congressional trades via the UW API recent-trades endpoint.

    The endpoint returns the latest trades; we paginate with the `date` param
    (passing the oldest transaction_date seen as the next page's cutoff) to
    walk backwards through history.
    """
    if not _token():
        logger.warning("UW_API_TOKEN missing — skipping UW API scrape")
        return []

    all_trades: list[dict] = []
    seen_ids: set[str] = set()
    date_cursor: str | None = None

    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        for page in range(max_pages):
            params: dict = {"limit": page_size}
            if date_cursor:
                params["date"] = date_cursor
            data = await _get(client, "/congress/recent-trades", params)
            if not data:
                break
            rows = data.get("data", []) if isinstance(data, dict) else data
            if not rows:
                break

            new_this_page = 0
            oldest_date = None
            for tx in rows:
                # Build a dedup key from politician + ticker + date + type
                key = f"{tx.get('politician_id')}|{tx.get('ticker')}|{tx.get('transaction_date')}|{tx.get('txn_type')}|{tx.get('amounts')}|{tx.get('filed_at_date')}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                parsed = _parse_trade(tx)
                if parsed:
                    all_trades.append(parsed)
                    new_this_page += 1
                td = tx.get("transaction_date")
                if td and (oldest_date is None or td < oldest_date):
                    oldest_date = td

            if page % 5 == 0:
                logger.info(f"UW API page {page}: +{new_this_page} (total {len(all_trades)})")

            # Advance cursor to walk backwards. If no progress, stop.
            if not oldest_date or oldest_date == date_cursor or new_this_page == 0:
                break
            date_cursor = oldest_date
            await asyncio.sleep(0.6)  # stay under 120 req/min

    logger.info(f"UW API total scraped: {len(all_trades)}")
    return all_trades


async def fetch_unusual_trades(types: str | None = None, limit: int = 500, max_pages: int = 5) -> list[dict]:
    """Fetch anomaly-flagged congressional trades.

    Args:
      types: comma-separated tags (committee_conflict, first_person_to_trade,
             low_marketcap, unusual_industry, unusually_large_trade,
             fec_donation_conflict). None = all.
    """
    if not _token():
        return []
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            params: dict = {"limit": limit, "page": page}
            if types:
                params["types"] = types
            data = await _get(client, "/congress/unusual-trades", params)
            if not data:
                break
            rows = data.get("data", []) if isinstance(data, dict) else data
            if not rows:
                break
            out.extend(rows)
            if len(rows) < limit:
                break
            await asyncio.sleep(0.6)
    logger.info(f"UW API unusual-trades: {len(out)} rows (types={types})")
    return out


async def fetch_late_reports(limit: int = 200) -> list[dict]:
    """Fetch politicians late on their STOCK Act filings."""
    if not _token():
        return []
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        data = await _get(client, "/congress/late-reports", {"limit": limit})
    if not data:
        return []
    return data.get("data", []) if isinstance(data, dict) else data


async def fetch_politicians(last_traded_within_months: int | None = None) -> list[dict]:
    """Fetch the full politician roster with per-member trade counts."""
    if not _token():
        return []
    params: dict = {}
    if last_traded_within_months:
        params["last_traded_within_months"] = last_traded_within_months
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        data = await _get(client, "/congress/politicians", params)
    if not data:
        return []
    return data.get("data", []) if isinstance(data, dict) else data
