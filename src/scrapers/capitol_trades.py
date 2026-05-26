"""Scraper for Capitol Trades (capitoltrades.com).

Fetches recent congressional stock trades from both House and Senate.
Data is embedded as JSON in React Server Component (RSC) flight data
within the Next.js App Router HTML response.
"""

import asyncio
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


def _extract_trade_array(chunk: str) -> list[dict] | None:
    """Extract the trade JSON array from an RSC chunk using bracket counting.

    The simple regex approach fails when text fields contain escaped quotes
    (e.g. comment fields with \\"Other\\"). This function finds the array start
    (by locating the first {"_issuerId" pattern) and then counts brackets to
    find the matching closing bracket, ensuring we get valid JSON.
    """
    # Find the start of the trade array: [{"_issuerId"
    arr_start = chunk.find('[{"_issuerId"')
    if arr_start == -1:
        return None

    # Use bracket counting to find the end of the array
    depth = 0
    in_string = False
    escape = False
    i = arr_start

    while i < len(chunk):
        c = chunk[i]

        if escape:
            escape = False
            i += 1
            continue

        if c == '\\':
            escape = True
            i += 1
            continue

        if c == '"' and not escape:
            in_string = not in_string
            i += 1
            continue

        if not in_string:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    # Found the matching close bracket
                    raw = chunk[arr_start:i + 1]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        # If full array fails, try extracting individual objects
                        return _extract_trades_individually(chunk)

        i += 1

    # Bracket counting didn't find a match; fall back to individual extraction
    return _extract_trades_individually(chunk)


def _extract_trades_individually(chunk: str) -> list[dict] | None:
    """Fallback: extract trade objects one at a time from the RSC chunk.

    Finds each {"_issuerId"... pattern and attempts to parse it as JSON.
    More resilient to malformed data in individual trade objects.
    """
    trades = []
    # Find each trade object start
    pattern = re.compile(r'\{"_issuerId"')
    for m in pattern.finditer(chunk):
        start = m.start()
        # Use bracket counting to find the end of this object
        depth = 0
        in_str = False
        esc = False
        for j in range(start, min(start + 5000, len(chunk))):
            c = chunk[j]
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if not in_str:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        raw = chunk[start:j + 1]
                        try:
                            trades.append(json.loads(raw))
                        except json.JSONDecodeError:
                            pass  # Skip malformed trade
                        break

    return trades if trades else None


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
            # Use a bracket-counting approach to find the array reliably,
            # since the simple regex can fail when text fields contain quotes.
            trade_list = _extract_trade_array(chunk)
            if trade_list is not None:
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
    max_pages: int = 400,
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

    consecutive_failures = 0
    max_failures = 5  # Stop after 5 consecutive failures (rate-limited or blocked)

    async with httpx.AsyncClient(
        timeout=45,
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
                consecutive_failures = 0
            except httpx.HTTPError as e:
                consecutive_failures += 1
                logger.error(
                    f"Failed to fetch Capitol Trades page {page}: {e} "
                    f"(failure {consecutive_failures}/{max_failures})"
                )
                if consecutive_failures >= max_failures:
                    logger.error("Too many consecutive failures, stopping scrape")
                    break
                # Back off on failure
                await asyncio.sleep(3)
                continue

            page_trades = extract_trades_from_html(resp.text)

            if not page_trades:
                logger.info(f"No more trades found at page {page}, stopping")
                break

            all_trades.extend(page_trades)

            if page % 25 == 0 or page <= 3:
                logger.info(
                    f"Page {page}/{max_pages}: fetched {len(page_trades)} trades "
                    f"(total: {len(all_trades)})"
                )

            # If we got significantly fewer than expected, we've likely reached the end.
            # Use a threshold (< 50%) to avoid false stops when a few trades are
            # skipped due to malformed data during extraction.
            if len(page_trades) < page_size * 0.5:
                logger.info(f"Got {len(page_trades)} < {page_size // 2} on page {page}, reached end")
                break

            # Rate-limit delay: 0.5s between pages to avoid getting blocked
            await asyncio.sleep(0.5)

    logger.info(f"Total Capitol Trades scraped: {len(all_trades)} from {page} pages")

    # Phase 2: Scrape per-politician filtered pages for top traders.
    # The general /trades endpoint is sorted by filing date, so older trades
    # from active politicians may not appear in the first N pages.
    # Fetch the top politicians list and scrape their full history.
    try:
        top_politicians = await _get_top_politician_ids(client)
        logger.info(f"Phase 2: scraping {len(top_politicians)} top politicians")
        for pol_id, pol_name in top_politicians:
            pol_trades = await _scrape_politician_trades(client, pol_id, page_size)
            if pol_trades:
                all_trades.extend(pol_trades)
                logger.info(f"  {pol_name} ({pol_id}): {len(pol_trades)} trades")
            await asyncio.sleep(0.5)
    except Exception as e:
        logger.error(f"Phase 2 politician scrape failed: {e}")

    logger.info(f"Total Capitol Trades scraped (with politician fills): {len(all_trades)}")
    return all_trades


async def _get_top_politician_ids(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Get politician IDs from the Capitol Trades politicians page.

    Paginates through all politician listing pages so we cover every member,
    not just the top 50 by recent activity. Without this, very active traders
    who happen to fall outside the top 50 (e.g. Rob Bresnahan with 600+
    trades but lower recent-7d activity) get only the handful of trades that
    appear in the general /trades feed.
    """
    politicians = []
    seen: set[str] = set()
    max_pages = 12  # 12 pages * 96 = up to 1152 politicians (covers all of Congress)

    for page in range(1, max_pages + 1):
        try:
            url = f"https://www.capitoltrades.com/politicians?page={page}&pageSize=96"
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

            match = re.search(
                r'self\.__next_f\.push\(\[1,"(.*?_odId.*?)"\]\)', html, re.DOTALL
            )
            if not match:
                logger.info(f"Politicians page {page}: no RSC data, stopping")
                break

            chunk = match.group(1).replace('\\"', '"').replace('\\n', '\n')
            pols = re.findall(
                r'"bioguideId":"([^"]+)".*?"firstName":"([^"]*)".*?"lastName":"([^"]*)"',
                chunk,
            )
            new_on_page = 0
            for bio_id, first, last in pols:
                if bio_id in seen:
                    continue
                seen.add(bio_id)
                politicians.append((bio_id, f"{first} {last}"))
                new_on_page += 1

            logger.info(f"Politicians page {page}: +{new_on_page} (total {len(politicians)})")
            if new_on_page == 0:
                break  # Hit the end of the listing
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Failed to fetch politician list page {page}: {e}")
            break

    return politicians


async def _scrape_politician_trades(
    client: httpx.AsyncClient,
    politician_id: str,
    page_size: int = 96,
) -> list[dict]:
    """Scrape all trades for a specific politician using the filtered endpoint."""
    all_trades = []
    for page in range(1, 20):  # Max 20 pages per politician (~1920 trades)
        url = f"{BASE_URL}?politician={politician_id}&page={page}&pageSize={page_size}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            break

        page_trades = extract_trades_from_html(resp.text)
        if not page_trades:
            break

        all_trades.extend(page_trades)

        if len(page_trades) < page_size * 0.5:
            break

        await asyncio.sleep(0.3)

    return all_trades
