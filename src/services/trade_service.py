"""Business logic for storing and querying trades."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session

from src.db.models import Member, Sector, Trade

logger = logging.getLogger(__name__)


def get_or_create_member(db: Session, name: str, chamber: str, **kwargs) -> Member:
    member = db.query(Member).filter(
        Member.name == name, Member.chamber == chamber
    ).first()
    if not member:
        member = Member(name=name, chamber=chamber, **kwargs)
        db.add(member)
        db.flush()
        # Commit immediately so the member survives any later rollbacks
        db.commit()
    else:
        # Update party if we have it now and didn't before
        if kwargs.get("party") and not member.party:
            member.party = kwargs["party"]
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
) -> list[Trade]:
    """Search trades with optional filters."""
    query = db.query(Trade).join(Member)

    if member_name:
        query = query.filter(Member.name.ilike(f"%{member_name}%"))
    if party:
        query = query.filter(Member.party.ilike(f"%{party}%"))
    if chamber:
        query = query.filter(Member.chamber == chamber)
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
