"""Direct scraper for Senate eFD (efdsearch.senate.gov).

This is the authoritative source for Senate financial disclosures.
Unlike senate.py (which pulls from a 3rd-party GitHub mirror that lags
days behind), this hits the official .gov search directly so the most
recent filings are available immediately.

Flow:
  1. Accept the terms agreement to get a session cookie
  2. POST to the report search endpoint asking for PTR (Periodic Transaction Report) filings
  3. Each result row links to a PTR view page that lists the underlying trades
  4. Parse those trade rows and convert to our internal format

The search returns recent PTRs by default. We cap to the last N days
so the daily scrape doesn't repeatedly re-fetch ancient data.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://efdsearch.senate.gov"
HOME = f"{BASE}/search/home/"
SEARCH = f"{BASE}/search/report/data/"

# Match the same amount buckets used elsewhere
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


def _parse_amount(s: str | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    s = s.strip()
    for label, (low, high) in AMOUNT_RANGES.items():
        if label in s:
            return float(low), float(high) if high else None
    nums = re.findall(r"[\d,]+", s)
    if len(nums) >= 2:
        return float(nums[0].replace(",", "")), float(nums[1].replace(",", ""))
    return None, None


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _normalize_tx_type(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower().strip()
    if "purchase" in t or "buy" in t:
        return "Purchase"
    if "sale" in t or "sell" in t:
        if "full" in t:
            return "Sale (Full)"
        if "partial" in t:
            return "Sale (Partial)"
        return "Sale"
    if "exchange" in t:
        return "Exchange"
    return s


def _normalize_owner(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower().strip()
    if "self" in t:
        return "Self"
    if "spouse" in t:
        return "Spouse"
    if "joint" in t:
        return "Joint"
    if "child" in t or "dependent" in t:
        return "Child"
    return s.title()


async def _accept_terms(client: httpx.AsyncClient) -> bool:
    """Hit the eFD home page and POST agreement so we get a session cookie."""
    try:
        r = await client.get(HOME)
        if r.status_code != 200:
            logger.warning(f"eFD home returned {r.status_code}")
            return False
        # Extract CSRF token from the form
        m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', r.text)
        token = m.group(1) if m else None
        # POST agreement
        r2 = await client.post(
            HOME,
            data={"csrfmiddlewaretoken": token or "", "prohibition_agreement": "1"},
            headers={"Referer": HOME},
        )
        return r2.status_code in (200, 302)
    except httpx.HTTPError as e:
        logger.error(f"eFD accept-terms failed: {e}")
        return False


async def _search_reports(
    client: httpx.AsyncClient, days_back: int = 30
) -> list[dict]:
    """POST the report search endpoint and return the data rows.

    The eFD search uses a DataTables AJAX endpoint that wants a bunch of
    form params. We ask for PTR-only filings (filer_type=Senator,
    report_types=11 for PTR) from the last N days.
    """
    start_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%m/%d/%Y")
    end_date = datetime.utcnow().strftime("%m/%d/%Y")
    payload = {
        "start": "0",
        "length": "500",
        "report_types": "[11]",  # 11 = Periodic Transaction Report
        "filer_types": "[]",
        "submitted_start_date": start_date + " 00:00:00",
        "submitted_end_date": end_date + " 23:59:59",
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
    }
    # Need CSRF token
    csrf = client.cookies.get("csrftoken")
    headers = {
        "X-CSRFToken": csrf or "",
        "Referer": f"{BASE}/search/",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        r = await client.post(SEARCH, data=payload, headers=headers)
        r.raise_for_status()
        return r.json().get("data", [])
    except httpx.HTTPError as e:
        logger.error(f"eFD search failed: {e}")
        return []
    except ValueError as e:
        logger.error(f"eFD search returned non-JSON: {e}")
        return []


async def _fetch_ptr_trades(
    client: httpx.AsyncClient, report_url: str, senator_name: str, filing_date: datetime | None
) -> list[dict]:
    """Fetch one PTR view page and parse out the trade rows."""
    try:
        r = await client.get(report_url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.debug(f"PTR fetch failed {report_url}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    # PTR pages render an HTML table of transactions
    rows = soup.select("table tbody tr")
    trades = []
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 7:
            continue
        # eFD PTR rows typically: # | Transaction Date | Owner | Ticker | Asset Name | Asset Type | Type | Amount | Comment
        # Column order varies slightly. Detect by looking for date in cells.
        try:
            # Find the transaction date column
            tx_date = None
            for c in cells:
                d = _parse_date(c)
                if d:
                    tx_date = d
                    break
            # Last cells usually hold type and amount
            tx_type = None
            amount_low = amount_high = None
            for c in cells:
                if any(k in c.lower() for k in ["purchase", "sale", "exchange"]):
                    tx_type = _normalize_tx_type(c)
                if "$" in c and ("-" in c or "over" in c.lower()):
                    al, ah = _parse_amount(c)
                    if al is not None:
                        amount_low, amount_high = al, ah
            # Asset description: the longest non-date, non-amount cell
            non_meta = [c for c in cells if not _parse_date(c) and "$" not in c and c not in ("S", "P", "E")]
            asset_desc = max(non_meta, key=len) if non_meta else ""
            # Ticker: short uppercase token
            ticker = None
            for c in cells:
                if 1 <= len(c) <= 6 and c.isupper() and c.isalpha():
                    ticker = c
                    break

            if not tx_date and not asset_desc:
                continue

            trades.append({
                "member_name": senator_name,
                "chamber": "Senate",
                "party": None,
                "state": None,
                "district": None,
                "filing_date": filing_date,
                "ticker": ticker,
                "asset_description": asset_desc,
                "asset_type": None,
                "transaction_type": tx_type,
                "transaction_date": tx_date,
                "amount_low": amount_low,
                "amount_high": amount_high,
                "owner": None,
                "raw_filing_url": report_url,
                "source": "senate_efd",
            })
        except Exception as e:
            logger.debug(f"PTR row parse error: {e}")
            continue

    return trades


async def scrape_senate_efd_direct(days_back: int = 30) -> list[dict]:
    """Pull recent Senate PTRs directly from efdsearch.senate.gov.

    Returns a list of trade dicts compatible with ingest_trades().
    """
    all_trades: list[dict] = []
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
        if not await _accept_terms(client):
            logger.warning("Senate eFD: could not accept terms, returning empty")
            return []

        reports = await _search_reports(client, days_back=days_back)
        logger.info(f"Senate eFD: search returned {len(reports)} reports")

        for row in reports:
            try:
                # DataTables row format: [first, last, _link_html, report_type, date]
                if len(row) < 5:
                    continue
                first = row[0].strip() if row[0] else ""
                last = row[1].strip() if row[1] else ""
                link_html = row[2] or ""
                date_str = row[4] if len(row) > 4 else ""

                m = re.search(r'href="([^"]+)"', link_html)
                if not m:
                    continue
                href = m.group(1)
                report_url = href if href.startswith("http") else f"{BASE}{href}"

                # Only follow paper PTRs (annotation_id paths) and HTML PTRs
                senator_name = f"{first} {last}".strip()
                filing_date = _parse_date(date_str)
                trades = await _fetch_ptr_trades(client, report_url, senator_name, filing_date)
                if trades:
                    all_trades.extend(trades)
                await asyncio.sleep(0.4)
            except Exception as e:
                logger.error(f"Senate eFD row failed: {e}")
                continue

    logger.info(f"Senate eFD: parsed {len(all_trades)} trades from direct source")
    return all_trades
