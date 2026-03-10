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
    else:
        # Update party if we have it now and didn't before
        if kwargs.get("party") and not member.party:
            member.party = kwargs["party"]
    return member


def enrich_sector(db: Session, ticker: str | None) -> tuple[str | None, str | None]:
    """Look up sector/industry for a ticker."""
    if not ticker:
        return None, None
    sector = db.query(Sector).filter(Sector.ticker == ticker).first()
    if sector:
        return sector.sector, sector.industry
    return None, None


def trade_exists(db: Session, member_id: int, trade_data: dict) -> bool:
    """Check for duplicate trade."""
    filters = [Trade.member_id == member_id]

    if trade_data.get("ticker"):
        filters.append(Trade.ticker == trade_data["ticker"])
    if trade_data.get("transaction_date"):
        filters.append(Trade.transaction_date == trade_data["transaction_date"])
    if trade_data.get("transaction_type"):
        filters.append(Trade.transaction_type == trade_data["transaction_type"])
    if trade_data.get("amount_low") is not None:
        filters.append(Trade.amount_low == trade_data["amount_low"])

    # Need at least ticker or asset_description to dedup meaningfully
    if not trade_data.get("ticker") and trade_data.get("asset_description"):
        filters.append(Trade.asset_description == trade_data["asset_description"])

    return db.query(Trade).filter(and_(*filters)).first() is not None


def ingest_trades(db: Session, raw_trades: list[dict]) -> int:
    """Ingest a list of scraped trades, deduplicating against existing records.

    Returns the count of new trades inserted.
    """
    new_count = 0

    for raw in raw_trades:
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
) -> list[Trade]:
    """Search trades with optional filters."""
    query = db.query(Trade).join(Member)

    if member_name:
        query = query.filter(Member.name.ilike(f"%{member_name}%"))
    if ticker:
        query = query.filter(Trade.ticker == ticker.upper())
    if sector:
        query = query.filter(Trade.sector.ilike(f"%{sector}%"))
    if transaction_type:
        query = query.filter(Trade.transaction_type.ilike(f"%{transaction_type}%"))
    if date_from:
        query = query.filter(Trade.transaction_date >= date_from)
    if date_to:
        query = query.filter(Trade.transaction_date <= date_to)

    return query.order_by(Trade.transaction_date.desc()).offset(offset).limit(limit).all()
