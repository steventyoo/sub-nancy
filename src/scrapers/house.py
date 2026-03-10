"""Scraper for House financial disclosures from disclosures-clerk.house.gov."""

import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://disclosures-clerk.house.gov"
SEARCH_URL = f"{BASE_URL}/FinancialDisclosure/ViewMemberSearchResult"

# Amount range mapping from disclosure text to numeric bounds
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
    amount_str = amount_str.strip()
    for label, (low, high) in AMOUNT_RANGES.items():
        if label in amount_str:
            return low, high
    return None, None


def parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def extract_ticker(asset_desc: str) -> str | None:
    """Try to extract a stock ticker from the asset description."""
    # Common patterns: "Apple Inc. (AAPL)", "[AAPL]", "AAPL - Apple Inc."
    match = re.search(r"\(([A-Z]{1,5})\)", asset_desc)
    if match:
        return match.group(1)
    match = re.search(r"\[([A-Z]{1,5})\]", asset_desc)
    if match:
        return match.group(1)
    return None


async def scrape_house_disclosures(year: int | None = None) -> list[dict]:
    """Scrape PTR (Periodic Transaction Report) filings from the House Clerk site.

    Returns a list of dicts with trade data.
    """
    if year is None:
        year = datetime.now().year

    trades = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # First, get the search page to grab any hidden form fields
        search_page = await client.get(f"{BASE_URL}/FinancialDisclosure/ViewSearch")

        # POST search for PTR filings
        form_data = {
            "LastName": "",
            "FilingYear": str(year),
            "State": "",
            "District": "",
            "ReportTypes": "TransactionReport",  # PTRs only
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{BASE_URL}/FinancialDisclosure/ViewSearch",
        }

        try:
            resp = await client.post(SEARCH_URL, data=form_data, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch House disclosures: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="library-table")
        if not table:
            logger.warning("No results table found in House search results")
            return []

        rows = table.find_all("tr")[1:]  # Skip header row
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            name = cols[0].get_text(strip=True)
            office = cols[1].get_text(strip=True)
            filing_year = cols[2].get_text(strip=True)
            filing_type = cols[3].get_text(strip=True)

            # Get the PDF link
            link = cols[0].find("a")
            pdf_url = None
            if link and link.get("href"):
                pdf_url = BASE_URL + link["href"] if link["href"].startswith("/") else link["href"]

            # Parse state/district from office field
            state, district = None, None
            if office:
                parts = office.split("-")
                if len(parts) >= 1:
                    state = parts[0].strip()[:2]
                if len(parts) >= 2:
                    district = parts[1].strip()

            trade = {
                "member_name": name,
                "chamber": "House",
                "state": state,
                "district": district,
                "filing_date": None,
                "raw_filing_url": pdf_url,
                "source": "house_clerk",
                # These fields need PDF parsing to fill — for now store the filing metadata
                "ticker": None,
                "asset_description": filing_type,
                "transaction_type": None,
                "transaction_date": None,
                "amount_low": None,
                "amount_high": None,
                "owner": None,
                "asset_type": None,
            }
            trades.append(trade)

    logger.info(f"Scraped {len(trades)} House PTR filings for {year}")
    return trades


async def scrape_house_xml_bulk(year: int | None = None) -> list[dict]:
    """Download and parse the annual bulk ZIP file for more structured data.

    The ZIP contains XML files with detailed transaction data.
    This is more reliable than scraping the search results page.
    """
    if year is None:
        year = datetime.now().year

    zip_url = f"{BASE_URL}/public_disc/financial-pdfs/{year}FD.zip"
    trades = []

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(zip_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(f"Bulk ZIP not available for {year}: {e}")
            return []

        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for name in zf.namelist():
                    if not name.endswith(".xml"):
                        continue
                    with zf.open(name) as f:
                        content = f.read().decode("utf-8", errors="replace")
                        parsed = _parse_house_xml(content)
                        trades.extend(parsed)
        except zipfile.BadZipFile:
            logger.error(f"Invalid ZIP file from {zip_url}")
            return []

    logger.info(f"Parsed {len(trades)} trades from House bulk ZIP for {year}")
    return trades


def _parse_house_xml(xml_content: str) -> list[dict]:
    """Parse a House financial disclosure XML file."""
    soup = BeautifulSoup(xml_content, "lxml")
    trades = []

    # The XML structure varies, but generally has Member info and Transactions
    member_el = soup.find("member")
    if not member_el:
        return []

    prefix = member_el.find("prefix")
    last = member_el.find("last")
    first = member_el.find("first")
    name = f"{first.text if first else ''} {last.text if last else ''}".strip()
    state_el = member_el.find("statedst")
    state = state_el.text[:2] if state_el and state_el.text else None
    district = state_el.text if state_el and state_el.text else None

    filing_date_el = soup.find("filingdate")
    filing_date = parse_date(filing_date_el.text) if filing_date_el and filing_date_el.text else None

    for tx in soup.find_all("transaction"):
        asset_el = tx.find("assettypecode") or tx.find("assettype")
        asset_type = asset_el.text.strip() if asset_el and asset_el.text else None

        desc_el = tx.find("assetdescription") or tx.find("asset")
        asset_desc = desc_el.text.strip() if desc_el and desc_el.text else ""

        tx_type_el = tx.find("transactiontypecode") or tx.find("transactiontype")
        tx_type = tx_type_el.text.strip() if tx_type_el and tx_type_el.text else None
        if tx_type:
            tx_type = {"P": "Purchase", "S": "Sale", "E": "Exchange"}.get(tx_type, tx_type)

        tx_date_el = tx.find("transactiondate")
        tx_date = parse_date(tx_date_el.text) if tx_date_el and tx_date_el.text else None

        amount_el = tx.find("amount") or tx.find("amountrangecode")
        amount_low, amount_high = parse_amount(amount_el.text if amount_el and amount_el.text else "")

        owner_el = tx.find("owner")
        owner = owner_el.text.strip() if owner_el and owner_el.text else None

        ticker = extract_ticker(asset_desc)

        trades.append({
            "member_name": name,
            "chamber": "House",
            "state": state,
            "district": district,
            "filing_date": filing_date,
            "ticker": ticker,
            "asset_description": asset_desc,
            "asset_type": asset_type,
            "transaction_type": tx_type,
            "transaction_date": tx_date,
            "amount_low": amount_low,
            "amount_high": amount_high,
            "owner": owner,
            "raw_filing_url": None,
            "source": "house_xml",
        })

    return trades
