"""Scraper for congressional committee assignments.

Fetches committee rosters from the official Congress.gov API (api.congress.gov)
and falls back to scraping clerk.house.gov and senate.gov if needed.

The Congress.gov API is free and requires no API key for basic endpoints.
"""

import asyncio
import logging
import re
from difflib import SequenceMatcher

import httpx

logger = logging.getLogger(__name__)

# Congress.gov API — free, no key required for committee endpoints
CONGRESS_API = "https://api.congress.gov/v3"
# Current congress number (119th: 2025-2027)
CURRENT_CONGRESS = 119

# Fallback: static committee list with codes for House and Senate
# These are the standing committees — covers ~95% of members
HOUSE_COMMITTEES = [
    ("HSAG", "House Committee on Agriculture"),
    ("HSAP", "House Committee on Appropriations"),
    ("HSAS", "House Committee on Armed Services"),
    ("HSBA", "House Committee on Financial Services"),
    ("HSBU", "House Committee on the Budget"),
    ("HSED", "House Committee on Education and the Workforce"),
    ("HSIF", "House Committee on Energy and Commerce"),
    ("HSFA", "House Committee on Foreign Affairs"),
    ("HSHA", "House Committee on House Administration"),
    ("HSII", "House Committee on Natural Resources"),
    ("HSIG", "House Permanent Select Committee on Intelligence"),
    ("HSJU", "House Committee on the Judiciary"),
    ("HSHM", "House Committee on Homeland Security"),
    ("HSGO", "House Committee on Oversight and Accountability"),
    ("HSRU", "House Committee on Rules"),
    ("HSSM", "House Committee on Small Business"),
    ("HSSY", "House Committee on Science, Space, and Technology"),
    ("HSPW", "House Committee on Transportation and Infrastructure"),
    ("HSVR", "House Committee on Veterans' Affairs"),
    ("HSWM", "House Committee on Ways and Means"),
]

SENATE_COMMITTEES = [
    ("SSAG", "Senate Committee on Agriculture, Nutrition, and Forestry"),
    ("SSAP", "Senate Committee on Appropriations"),
    ("SSAS", "Senate Committee on Armed Services"),
    ("SSBK", "Senate Committee on Banking, Housing, and Urban Affairs"),
    ("SSBU", "Senate Committee on the Budget"),
    ("SSCM", "Senate Committee on Commerce, Science, and Transportation"),
    ("SSEG", "Senate Committee on Energy and Natural Resources"),
    ("SSEV", "Senate Committee on Environment and Public Works"),
    ("SSFI", "Senate Committee on Finance"),
    ("SSFR", "Senate Committee on Foreign Relations"),
    ("SSHR", "Senate Committee on Health, Education, Labor, and Pensions"),
    ("SSHI", "Senate Committee on Homeland Security and Governmental Affairs"),
    ("SSGA", "Senate Committee on Indian Affairs"),
    ("SSIN", "Senate Select Committee on Intelligence"),
    ("SSJU", "Senate Committee on the Judiciary"),
    ("SSRA", "Senate Committee on Rules and Administration"),
    ("SSSB", "Senate Committee on Small Business and Entrepreneurship"),
    ("SSVA", "Senate Committee on Veterans' Affairs"),
]


async def scrape_committees() -> list[dict]:
    """Scrape committee rosters. Returns list of dicts with committee + member info.

    Returns:
        [
            {
                "committee_name": "House Committee on Energy and Commerce",
                "committee_code": "HSIF",
                "chamber": "House",
                "member_name": "Robert E. Latta",
                "role": "Chair",
            },
            ...
        ]
    """
    results = []

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Try Congress.gov API first
        api_results = await _scrape_congress_api(client)
        if api_results:
            logger.info(f"Congress.gov API: {len(api_results)} committee assignments")
            return api_results

        # Fallback: scrape individual committee pages
        logger.info("Congress.gov API failed, falling back to direct scraping")
        for code, name in HOUSE_COMMITTEES + SENATE_COMMITTEES:
            chamber = "House" if code.startswith("HS") else "Senate"
            members = await _scrape_committee_page(client, code, chamber)
            for m in members:
                results.append({
                    "committee_name": name,
                    "committee_code": code,
                    "chamber": chamber,
                    "member_name": m["name"],
                    "role": m["role"],
                })
            # Rate limit
            await asyncio.sleep(0.5)

    logger.info(f"Scraped {len(results)} committee assignments")
    return results


async def _scrape_congress_api(client: httpx.AsyncClient) -> list[dict]:
    """Use the Congress.gov API to get committee members."""
    results = []

    all_committees = HOUSE_COMMITTEES + SENATE_COMMITTEES

    for code, name in all_committees:
        chamber = "House" if code.startswith("HS") else "Senate"
        chamber_api = "house" if chamber == "House" else "senate"

        url = f"{CONGRESS_API}/committee/{chamber_api}/{code}?format=json"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue

            data = resp.json()
            committee_data = data.get("committee", {})

            # Get current members from subresource
            members_url = committee_data.get("currentMembers", {}).get("url")
            if not members_url:
                # Try direct members endpoint
                members_url = f"{CONGRESS_API}/committee/{chamber_api}/{code}/members?format=json"

            resp2 = await client.get(members_url if members_url.startswith("http") else f"{CONGRESS_API}{members_url}")
            if resp2.status_code != 200:
                continue

            members_data = resp2.json()
            for member in members_data.get("members", []):
                member_name = member.get("name", "")
                # Congress.gov returns "Last, First" format — normalize
                if "," in member_name:
                    parts = member_name.split(",", 1)
                    member_name = f"{parts[1].strip()} {parts[0].strip()}"

                role = "Member"
                if member.get("isChair") or member.get("role", "").lower() == "chair":
                    role = "Chair"
                elif member.get("isRankingMember") or "ranking" in member.get("role", "").lower():
                    role = "Ranking Member"

                results.append({
                    "committee_name": name,
                    "committee_code": code,
                    "chamber": chamber,
                    "member_name": member_name,
                    "role": role,
                })

            await asyncio.sleep(0.3)  # Rate limit

        except Exception as e:
            logger.warning(f"Congress.gov API error for {code}: {e}")
            continue

    return results


async def _scrape_committee_page(
    client: httpx.AsyncClient, code: str, chamber: str
) -> list[dict]:
    """Fallback: scrape committee member list from congress.gov HTML."""
    slug = code.lower()
    url = f"https://www.congress.gov/committee/{chamber.lower()}/{slug}"

    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return []

        text = resp.text
        members = []

        # Parse member names from the committee page HTML
        # Congress.gov lists members in <a> tags within member tables
        name_pattern = re.compile(
            r'class="[^"]*member[^"]*"[^>]*>.*?<a[^>]*>([^<]+)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in name_pattern.finditer(text):
            name = match.group(1).strip()
            if name and len(name) > 3:
                role = "Member"
                # Check surrounding context for role
                context = text[max(0, match.start() - 200): match.end() + 50]
                if "chair" in context.lower() and "ranking" not in context.lower():
                    role = "Chair"
                elif "ranking" in context.lower():
                    role = "Ranking Member"
                members.append({"name": name, "role": role})

        return members

    except Exception as e:
        logger.warning(f"Failed to scrape {url}: {e}")
        return []


def fuzzy_match_member(scraped_name: str, db_names: list[str], threshold: float = 0.7) -> str | None:
    """Fuzzy match a scraped member name to existing DB member names.

    Congressional names come in many formats:
    - "Robert E. Latta" vs "Robert Latta" vs "Bob Latta"
    - "Nancy Pelosi" vs "Hon. Nancy Pelosi"
    """
    scraped_clean = _clean_name(scraped_name)
    best_match = None
    best_score = 0

    for db_name in db_names:
        db_clean = _clean_name(db_name)
        score = SequenceMatcher(None, scraped_clean.lower(), db_clean.lower()).ratio()

        # Also check last-name match as a boost
        scraped_last = scraped_clean.split()[-1] if scraped_clean.split() else ""
        db_last = db_clean.split()[-1] if db_clean.split() else ""
        if scraped_last.lower() == db_last.lower():
            score += 0.2  # Boost for matching last name

        if score > best_score:
            best_score = score
            best_match = db_name

    if best_score >= threshold:
        return best_match
    return None


def _clean_name(name: str) -> str:
    """Remove prefixes/suffixes and normalize name."""
    prefixes = ["hon.", "rep.", "sen.", "mr.", "mrs.", "ms.", "dr.", "representative", "senator"]
    suffixes = ["jr.", "jr", "sr.", "sr", "iii", "ii", "iv", "m.d.", "ph.d."]

    parts = name.strip().split()
    # Remove prefixes
    while parts and parts[0].lower().rstrip(".") in [p.rstrip(".") for p in prefixes]:
        parts = parts[1:]
    # Remove suffixes
    while parts and parts[-1].lower().rstrip(".") in [s.rstrip(".") for s in suffixes]:
        parts = parts[:-1]
    # Remove middle initials (single letter followed by period)
    parts = [p for p in parts if not (len(p) <= 2 and p.endswith("."))]

    return " ".join(parts)
