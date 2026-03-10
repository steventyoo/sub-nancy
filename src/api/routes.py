"""FastAPI routes for the congressional trade tracker."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from sqlalchemy import func

from src.db.database import get_db
from src.db.models import Member, Subscriber, Trade
from src.services.query_service import natural_language_query
from src.services.trade_service import search_trades

router = APIRouter(prefix="/api")


# --- Schemas ---


class TradeOut(BaseModel):
    id: int
    member_name: str
    chamber: str
    transaction_date: datetime | None
    filing_date: datetime | None
    ticker: str | None
    asset_description: str | None
    transaction_type: str | None
    amount_low: float | None
    amount_high: float | None
    owner: str | None
    sector: str | None
    industry: str | None

    model_config = {"from_attributes": True}


class QueryRequest(BaseModel):
    question: str


class SubscribeRequest(BaseModel):
    email: EmailStr
    filters: dict | None = None


class MemberOut(BaseModel):
    id: int
    name: str
    chamber: str
    state: str | None
    district: str | None
    party: str | None
    active: bool

    model_config = {"from_attributes": True}


# --- Endpoints ---


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    member: str | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    transaction_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt = datetime.strptime(date_to, "%Y-%m-%d") if date_to else None

    trades = search_trades(db, member, ticker, sector, transaction_type, df, dt, limit, offset)
    results = []
    for t in trades:
        results.append(
            TradeOut(
                id=t.id,
                member_name=t.member.name,
                chamber=t.member.chamber,
                transaction_date=t.transaction_date,
                filing_date=t.filing_date,
                ticker=t.ticker,
                asset_description=t.asset_description,
                transaction_type=t.transaction_type,
                amount_low=t.amount_low,
                amount_high=t.amount_high,
                owner=t.owner,
                sector=t.sector,
                industry=t.industry,
            )
        )
    return results


@router.get("/trades/recent", response_model=list[TradeOut])
def recent_trades(
    member: str | None = None,
    ticker: str | None = None,
    transaction_type: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get the most recent trades by transaction date with optional filters."""
    query = db.query(Trade).join(Member)
    if member:
        query = query.filter(Member.name == member)
    if ticker:
        query = query.filter(Trade.ticker == ticker.upper())
    if transaction_type:
        query = query.filter(Trade.transaction_type.ilike(f"%{transaction_type}%"))
    trades = (
        query
        .order_by(Trade.transaction_date.desc().nullslast())
        .limit(limit)
        .all()
    )
    results = []
    for t in trades:
        results.append(
            TradeOut(
                id=t.id,
                member_name=t.member.name,
                chamber=t.member.chamber,
                transaction_date=t.transaction_date,
                filing_date=t.filing_date,
                ticker=t.ticker,
                asset_description=t.asset_description,
                transaction_type=t.transaction_type,
                amount_low=t.amount_low,
                amount_high=t.amount_high,
                owner=t.owner,
                sector=t.sector,
                industry=t.industry,
            )
        )
    return results


@router.get("/filters/members")
def filter_members(db: Session = Depends(get_db)):
    """Get distinct member names for dropdown filter."""
    rows = (
        db.query(Member.name)
        .join(Trade, Trade.member_id == Member.id)
        .group_by(Member.name)
        .order_by(Member.name)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/filters/tickers")
def filter_tickers(db: Session = Depends(get_db)):
    """Get distinct tickers for dropdown filter."""
    rows = (
        db.query(Trade.ticker)
        .filter(Trade.ticker.isnot(None), Trade.ticker != "")
        .group_by(Trade.ticker)
        .order_by(Trade.ticker)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/stats")
def trade_stats(db: Session = Depends(get_db)):
    """Get basic stats about the trade database."""
    total = db.query(Trade).count()
    members_count = db.query(Member).count()
    latest = (
        db.query(Trade)
        .order_by(Trade.transaction_date.desc().nullslast())
        .first()
    )
    latest_date = latest.transaction_date.isoformat() if latest and latest.transaction_date else None
    return {
        "total_trades": total,
        "total_members": members_count,
        "latest_trade_date": latest_date,
    }


@router.post("/query")
async def query_trades(req: QueryRequest, db: Session = Depends(get_db)):
    result = await natural_language_query(db, req.question)
    return result


@router.get("/members", response_model=list[MemberOut])
def list_members(chamber: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Member)
    if chamber:
        query = query.filter(Member.chamber == chamber)
    return query.order_by(Member.name).all()


@router.post("/subscribe")
def subscribe(req: SubscribeRequest, db: Session = Depends(get_db)):
    existing = db.query(Subscriber).filter(Subscriber.email == req.email).first()
    if existing:
        existing.active = True
        if req.filters:
            existing.filters = req.filters
        db.commit()
        return {"message": "Subscription reactivated", "id": existing.id}

    sub = Subscriber(email=req.email)
    if req.filters:
        sub.filters = req.filters
    db.add(sub)
    db.commit()
    return {"message": "Subscribed successfully", "id": sub.id}


@router.delete("/subscribe/{subscriber_id}")
def unsubscribe(subscriber_id: int, db: Session = Depends(get_db)):
    sub = db.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    sub.active = False
    db.commit()
    return {"message": "Unsubscribed successfully"}


@router.post("/send-emails")
def trigger_emails(db: Session = Depends(get_db)):
    """Trigger email notifications (used by Vercel cron)."""
    from src.services.email_service import send_daily_notifications

    send_daily_notifications(db)
    return {"message": "Email job completed"}


def _run_scrape():
    """Run scrape in background thread so it doesn't block the server."""
    import asyncio
    import logging

    from src.db.database import SessionLocal
    from src.scrapers.capitol_trades import scrape_capitol_trades
    from src.scrapers.finnhub import scrape_finnhub_congress
    from src.scrapers.house import scrape_house_disclosures
    from src.scrapers.senate import scrape_senate_disclosures
    from src.services.trade_service import ingest_trades

    logger = logging.getLogger(__name__)
    logger.info("Background scrape started")

    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()
        capitol_trades = loop.run_until_complete(scrape_capitol_trades(max_pages=30))
        house_trades = loop.run_until_complete(scrape_house_disclosures())
        senate_trades = loop.run_until_complete(scrape_senate_disclosures())
        finnhub_trades = loop.run_until_complete(scrape_finnhub_congress())
        loop.close()

        all_trades = capitol_trades + house_trades + senate_trades + finnhub_trades
        new_count = ingest_trades(db, all_trades)
        logger.info(
            f"Background scrape complete: {new_count} new trades "
            f"(capitol={len(capitol_trades)}, house={len(house_trades)}, "
            f"senate={len(senate_trades)}, finnhub={len(finnhub_trades)})"
        )
    except Exception as e:
        logger.error(f"Background scrape failed: {e}")
    finally:
        db.close()


@router.post("/scrape")
def trigger_scrape(background_tasks: BackgroundTasks):
    """Manually trigger a scrape of all sources (runs in background)."""
    background_tasks.add_task(_run_scrape)
    return {"message": "Scrape started in background. Check logs for progress."}
