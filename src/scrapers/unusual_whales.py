"""Scraper for Unusual Whales (unusualwhales.com/politics).

Pulls congressional trades from the public politics page. The page embeds
trade data in a __NEXT_DATA__ script tag — no auth required (UW's paid API
needs a Bearer token, but the public HTML page does not).

UW is an important supplementary source because it sometimes catches filings
that Capitol Trades misses or delays, and it includes direct links to the
underlying Senate/House disclosure PDFs.
"""

import asyncio
import json
import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://unusualwhales.com/politics"


def _parse_amount(amounts_str: str | None) -> tuple[float | None, float | None]:
    """Parse '$1,001 - $15,000' style range into (low, high) floats."""
    if not amounts_str:
        return None, None
    nums = re.findall(r"[\d,]+", amounts_str.replace("$", ""))
    if len(nums) >= 2:
        try:
            return float(nums[0].replace(",", "")), float(nums[1].replace(",", ""))
        except ValueError:
            return None, None
    if len(nums) == 1:
        try:
            v = float(nums[0].replace(",", ""))
            return v, v
        except ValueError:
            pass
    return None, None


def _normalize_tx_type(txn: str | None) -> str | None:
    if not txn:
        return None
    t = txn.lower().strip()
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
    return txn


def _normalize_owner(affil: str | None) -> str | None:
    if not affil:
        return None
    a = affil.lower().strip()
    if a == "self":
        return "Self"
    if "spouse" in a:
        return "Spouse"
    if "joint" in a:
        return "Joint"
    if "child" in a or "dependent" in a:
        return "Child"
    if "undisclosed" in a or "unknown" in a:
        return None
    return affil.title()


def _normalize_chamber(c: str | None) -> str:
    if not c:
        return "Senate"
    return "House" if c.lower().strip() == "house" else "Senate"


def _normalize_party(p: str | None) -> str | None:
    if not p:
        return None
    return p.strip().title()


def _clean_member_name(name: str | None, reporter: str | None) -> str | None:
    """Prefer the cleaner `name` field, fall back to `reporter` minus honorifics.

    Examples:
      name="Jim Banks", reporter="James Banks" → "Jim Banks"
      name="Susie Lee", reporter="Hon. Susie Lee" → "Susie Lee"
    """
    return (name or reporter or "").strip() or None


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    # Try common formats
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Try fromisoformat
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _parse_trade(tx: dict) -> dict | None:
    """Parse a single UW trade dict into our internal format."""
    try:
        member_name = _clean_member_name(tx.get("name"), tx.get("reporter"))
        if not member_name:
            return None

        symbol = (tx.get("symbol") or "").strip() or None
        if symbol and (symbol == "--" or len(symbol) > 10):
            symbol = None

        amount_low, amount_high = _parse_amount(tx.get("amounts"))
        tx_date = _parse_date(tx.get("transaction_date"))
        filing_date = _parse_date(tx.get("filed_at_date") or tx.get("created_at"))

        return {
            "member_name": member_name,
            "chamber": _normalize_chamber(tx.get("current_chamber") or tx.get("member_type")),
            "party": _normalize_party(tx.get("current_party")),
            "state": (tx.get("current_district") or "").split("-")[0].upper() or None,
            "district": tx.get("current_district"),
            "filing_date": filing_date,
            "ticker": symbol,
            "asset_description": tx.get("issuer") or tx.get("notes") or "",
            "asset_type": tx.get("asset"),
            "transaction_type": _normalize_tx_type(tx.get("txn_type")),
            "transaction_date": tx_date,
            "amount_low": amount_low,
            "amount_high": amount_high,
            "owner": _normalize_owner(tx.get("affiliation")),
            "raw_filing_url": tx.get("link_url"),
            "source": "unusual_whales",
        }
    except Exception as e:
        logger.debug(f"Skipping UW trade entry: {e}")
        return None


def _extract_trades_from_html(html: str) -> list[dict]:
    """Extract the trade_data array from UW's __NEXT_DATA__ JSON blob."""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        logger.warning("UW page: __NEXT_DATA__ not found")
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.error(f"UW __NEXT_DATA__ JSON decode failed: {e}")
        return []

    raw_trades = (
        data.get("props", {}).get("pageProps", {}).get("trade_data", [])
    )
    parsed = []
    for tx in raw_trades:
        p = _parse_trade(tx)
        if p:
            parsed.append(p)
    return parsed


async def scrape_unusual_whales(max_pages: int = 50) -> list[dict]:
    """Scrape recent congressional trades from Unusual Whales.

    Args:
        max_pages: Maximum politics pages to walk (each ~100 trades).

    Returns:
        List of trade dicts compatible with ingest_trades().
    """
    all_trades: list[dict] = []
    consecutive_empty = 0

    async with httpx.AsyncClient(
        timeout=45,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}?page={page}" if page > 1 else BASE_URL
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(f"UW page {page} fetch failed: {e}")
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                continue

            page_trades = _extract_trades_from_html(resp.text)
            if not page_trades:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.info(f"UW: page {page} empty, stopping")
                    break
                continue
            consecutive_empty = 0
            all_trades.extend(page_trades)

            if page % 10 == 0 or page <= 3:
                logger.info(f"UW page {page}/{max_pages}: +{len(page_trades)} (total {len(all_trades)})")

            # Stop if a page returned far less than expected (likely end of feed)
            if len(page_trades) < 30:
                logger.info(f"UW page {page}: only {len(page_trades)} trades, reached end")
                break

            await asyncio.sleep(0.4)

    logger.info(f"UW total scraped: {len(all_trades)}")
    return all_trades
