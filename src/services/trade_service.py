"""Business logic for storing and querying trades."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session

from src.db.models import Member, Sector, Trade

logger = logging.getLogger(__name__)


_TITLE_TOKENS = {"hon", "hon.", "hon..", "rep", "rep.", "sen", "sen.", "dr", "dr.",
                 "mr", "mr.", "mrs", "mrs.", "ms", "ms."}
_SUFFIX_TOKENS = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def normalize_member_name(raw: str) -> str:
    """Normalize a politician name into a canonical "First Last" form.

    Sources publish names in wildly different formats:
      - Capitol Trades: "Robert Bresnahan"
      - House Clerk:    "Bresnahan, Hon.. Rob"
      - Senate Disc:    "Bresnahan, Robert"
      - Variants:       "Michael F Bennet" vs "Michael Bennet"

    Returns a stable key suitable for matching across sources. Strategy:
      1. If name contains a comma, flip "Last, First" → "First Last"
      2. Strip honorifics (Hon., Rep., Sen., Dr., Mr., Mrs.)
      3. Strip trailing suffixes (Jr., II, III)
      4. Collapse multiple spaces
      5. Title-case for canonical display
    """
    if not raw:
        return raw
    s = raw.strip()

    # Flip "Last, First..." to "First... Last"
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2:
            s = f"{parts[1]} {parts[0]}"

    # Tokenize, strip honorifics + suffixes, drop empty tokens
    tokens = []
    for tok in s.split():
        low = tok.lower().rstrip(".,")
        if low in _TITLE_TOKENS or low + "." in _TITLE_TOKENS:
            continue
        if low in _SUFFIX_TOKENS:
            continue
        # Drop standalone middle initials like "F" or "F." for the matching key —
        # they were the cause of "Michael Bennet" vs "Michael F Bennet" splits.
        if len(low) <= 2 and low.endswith("."):
            continue
        if len(low) == 1 and tok.isalpha():
            continue
        tokens.append(tok)

    cleaned = " ".join(tokens)
    # Title-case (handles "Mcconnell" → "Mcconnell" etc. — good enough)
    return cleaned.title()


def get_or_create_member(db: Session, name: str, chamber: str, **kwargs) -> Member:
    """Find an existing member by normalized name OR create a new one.

    Matches across sources that publish names in different formats by
    normalizing both sides before comparing. Falls back to exact-match for
    safety (e.g. two real different people named the same).
    """
    canonical = normalize_member_name(name) if name else name

    # First try exact match (cheap)
    member = db.query(Member).filter(
        Member.name == canonical, Member.chamber == chamber
    ).first()

    # Then try matching any existing member whose normalized name == canonical
    if not member:
        candidates = db.query(Member).filter(Member.chamber == chamber).all()
        for c in candidates:
            if normalize_member_name(c.name) == canonical:
                member = c
                # Upgrade the stored name to the cleaner canonical form
                if c.name != canonical:
                    c.name = canonical
                break

    # Third: match by (last_name, chamber, state) when state is known on both
    # sides. Catches nickname variants the normalized-name pass can't:
    # "Jerry Moran" + "Gerald Moran" (state=KS) → same Senator.
    if not member and canonical:
        incoming_state = kwargs.get("state")
        incoming_last = canonical.split()[-1].lower() if canonical.split() else None
        if incoming_last and incoming_state:
            candidates = (
                db.query(Member)
                .filter(Member.chamber == chamber, Member.state == incoming_state)
                .all()
            )
            for c in candidates:
                c_last = c.name.split()[-1].lower() if c.name.split() else None
                if c_last == incoming_last:
                    member = c
                    break
        # If we have a last name but no state on incoming, try matching against
        # an existing row that DOES have a state set with the same lastname.
        elif incoming_last and not incoming_state:
            candidates = (
                db.query(Member)
                .filter(Member.chamber == chamber, Member.state.isnot(None))
                .all()
            )
            for c in candidates:
                c_last = c.name.split()[-1].lower() if c.name.split() else None
                if c_last == incoming_last:
                    member = c
                    break

    if not member:
        member = Member(name=canonical, chamber=chamber, **kwargs)
        db.add(member)
        db.flush()
        db.commit()
    else:
        # Update party/state/district if we have them now and didn't before
        for key in ("party", "state", "district"):
            val = kwargs.get(key)
            if val and not getattr(member, key, None):
                setattr(member, key, val)
    return member


def enrich_sector(db: Session, ticker: str | None) -> tuple[str | None, str | None]:
    """Look up sector/industry for a ticker. Falls back to yfinance API if not in seed data."""
    if not ticker:
        return None, None
    # Skip tickers that are clearly not stocks (crypto, special prefixes)
    if ticker.startswith("$$") or len(ticker) > 10:
        return None, None
    sector_row = db.query(Sector).filter(Sector.ticker == ticker).first()
    if sector_row:
        return sector_row.sector, sector_row.industry

    # Fallback: try yfinance for unknown tickers
    sector, industry, company_name = _lookup_yfinance(ticker)
    if sector:
        # Cache it in the sectors table for future lookups
        new_sector = Sector(
            ticker=ticker,
            sector=sector,
            industry=industry,
            company_name=company_name,
        )
        try:
            db.add(new_sector)
            db.flush()
            logger.info(f"Auto-enriched sector for {ticker}: {sector} / {industry}")
        except Exception:
            db.rollback()  # Duplicate ticker race condition
        return sector, industry

    return None, None


_yfinance_failed_cache: set[str] = set()


def _lookup_yfinance(ticker: str) -> tuple[str | None, str | None, str | None]:
    """Look up sector/industry from yfinance. Returns (sector, industry, company_name)."""
    # Skip tickers we've already failed on this session
    if ticker in _yfinance_failed_cache:
        return None, None, None
    # Skip tickers with special characters that yfinance can't handle
    if "/" in ticker or " " in ticker:
        _yfinance_failed_cache.add(ticker)
        return None, None, None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = info.get("sector")
        industry = info.get("industry")
        company_name = info.get("shortName") or info.get("longName")
        if sector:
            return sector, industry, company_name
        _yfinance_failed_cache.add(ticker)
    except Exception as e:
        _yfinance_failed_cache.add(ticker)
        logger.debug(f"yfinance lookup failed for {ticker}: {e}")
    return None, None, None


def trade_exists(db: Session, member_id: int, trade_data: dict) -> bool:
    """Check for duplicate trade.

    Uses a tighter dedup key that includes filing_date and owner so that
    legitimate same-day trades on the same ticker aren't dropped.
    """
    filters = [Trade.member_id == member_id]

    if trade_data.get("ticker"):
        filters.append(Trade.ticker == trade_data["ticker"])
    if trade_data.get("transaction_date"):
        filters.append(Trade.transaction_date == trade_data["transaction_date"])
    if trade_data.get("transaction_type"):
        filters.append(Trade.transaction_type == trade_data["transaction_type"])
    if trade_data.get("amount_low") is not None:
        filters.append(Trade.amount_low == trade_data["amount_low"])

    # Include filing_date to distinguish separate filings on the same day
    if trade_data.get("filing_date"):
        filters.append(Trade.filing_date == trade_data["filing_date"])

    # Include owner to distinguish self vs spouse vs child trades
    if trade_data.get("owner"):
        filters.append(Trade.owner == trade_data["owner"])

    # Need at least ticker or asset_description to dedup meaningfully
    if not trade_data.get("ticker") and trade_data.get("asset_description"):
        filters.append(Trade.asset_description == trade_data["asset_description"])

    return db.query(Trade).filter(and_(*filters)).first() is not None


def ingest_trades(db: Session, raw_trades: list[dict]) -> int:
    """Ingest a list of scraped trades, deduplicating against existing records.

    Commits in batches to avoid autoflush FK violations on large imports.
    Returns the count of new trades inserted.
    """
    new_count = 0
    batch_size = 500

    for i, raw in enumerate(raw_trades):
        try:
            with db.no_autoflush:
                member = get_or_create_member(
                    db,
                    name=raw["member_name"],
                    chamber=raw["chamber"],
                    state=raw.get("state"),
                    district=raw.get("district"),
                    party=raw.get("party"),
                )

            sector, industry = enrich_sector(db, raw.get("ticker"))

            trade_data = {
                "ticker": raw.get("ticker"),
                "transaction_date": raw.get("transaction_date"),
                "transaction_type": raw.get("transaction_type"),
                "amount_low": raw.get("amount_low"),
                "amount_high": raw.get("amount_high"),
                "asset_description": raw.get("asset_description"),
                "filing_date": raw.get("filing_date"),
                "owner": raw.get("owner"),
            }

            if trade_exists(db, member.id, trade_data):
                continue

            trade = Trade(
                member_id=member.id,
                transaction_date=raw.get("transaction_date"),
                filing_date=raw.get("filing_date"),
                ticker=raw.get("ticker"),
                asset_description=raw.get("asset_description"),
                asset_type=raw.get("asset_type"),
                transaction_type=raw.get("transaction_type"),
                amount_low=raw.get("amount_low"),
                amount_high=raw.get("amount_high"),
                owner=raw.get("owner"),
                sector=sector,
                industry=industry,
                source_url=raw.get("source_url"),
                raw_filing_url=raw.get("raw_filing_url"),
            )
            db.add(trade)
            new_count += 1

            # Commit in batches to keep session clean
            if new_count % batch_size == 0:
                db.commit()
                logger.info(f"Batch commit: {new_count} new trades so far (processed {i + 1}/{len(raw_trades)})")

        except Exception as e:
            logger.warning(f"Skipping trade {i}: {e}")
            db.rollback()
            continue

    db.commit()
    logger.info(f"Ingested {new_count} new trades (out of {len(raw_trades)} scraped)")
    return new_count


def get_recent_trades(db: Session, hours: int = 24) -> list[Trade]:
    """Get trades added in the last N hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    return (
        db.query(Trade)
        .filter(Trade.created_at >= cutoff)
        .order_by(Trade.created_at.desc())
        .all()
    )


def search_trades(
    db: Session,
    member_name: str | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    transaction_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
    owner: str | None = None,
    party: str | None = None,
    chamber: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
) -> list[Trade]:
    """Search trades with optional filters."""
    query = db.query(Trade).join(Member)

    if member_name:
        query = query.filter(Member.name.ilike(f"%{member_name}%"))
    if party:
        query = query.filter(Member.party.ilike(f"%{party}%"))
    if chamber:
        query = query.filter(Member.chamber == chamber)
    if min_amount is not None:
        # Trade amount range overlaps [min_amount, +inf) when amount_high >= min_amount
        query = query.filter(Trade.amount_high >= min_amount)
    if max_amount is not None:
        # Trade amount range overlaps (-inf, max_amount] when amount_low <= max_amount
        query = query.filter(Trade.amount_low <= max_amount)
    if ticker:
        query = query.filter(Trade.ticker == ticker.upper())
    if sector:
        query = query.filter(Trade.sector.ilike(f"%{sector}%"))
    if transaction_type:
        query = query.filter(Trade.transaction_type.ilike(f"%{transaction_type}%"))
    if owner:
        query = query.filter(Trade.owner.ilike(f"%{owner}%"))
    if date_from:
        query = query.filter(Trade.transaction_date >= date_from)
    if date_to:
        query = query.filter(Trade.transaction_date <= date_to)

    return query.order_by(Trade.transaction_date.desc()).offset(offset).limit(limit).all()
