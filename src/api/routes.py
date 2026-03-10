"""FastAPI routes for the congressional trade tracker."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

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


@router.post("/scrape")
async def trigger_scrape(db: Session = Depends(get_db)):
    """Manually trigger a scrape of all sources."""
    from src.scrapers.capitol_trades import scrape_capitol_trades
    from src.scrapers.house import scrape_house_disclosures
    from src.scrapers.senate import scrape_senate_disclosures
    from src.scrapers.finnhub import scrape_finnhub_congress
    from src.services.trade_service import ingest_trades

    capitol_trades = await scrape_capitol_trades(max_pages=30)
    house_trades = await scrape_house_disclosures()
    senate_trades = await scrape_senate_disclosures()
    finnhub_trades = await scrape_finnhub_congress()

    all_trades = capitol_trades + house_trades + senate_trades + finnhub_trades
    new_count = ingest_trades(db, all_trades)

    return {
        "message": f"Scrape complete. {new_count} new trades ingested.",
        "capitol_trades_scraped": len(capitol_trades),
        "house_scraped": len(house_trades),
        "senate_scraped": len(senate_trades),
        "finnhub_scraped": len(finnhub_trades),
        "new_trades": new_count,
    }
