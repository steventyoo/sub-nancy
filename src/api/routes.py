"""FastAPI routes for the congressional trade tracker."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from sqlalchemy import case, func

from src.db.database import get_db
from src.db.models import Member, Subscriber, Trade
from src.services.query_service import natural_language_query
from src.services.trade_service import search_trades

router = APIRouter(prefix="/api")


# --- Schemas ---


class TradeOut(BaseModel):
    id: int
    member_name: str
    party: str | None
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
                party=t.member.party,
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
                party=t.member.party,
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


@router.get("/dashboard")
def dashboard_stats(db: Session = Depends(get_db)):
    """Dashboard summary: top tickers, most active members, buy/sell ratio, recent activity."""
    from datetime import timedelta

    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    seven_days_ago = now - timedelta(days=7)

    # Total counts
    total_trades = db.query(Trade).count()
    total_members = db.query(Member).count()
    # Members who actually filed (have trades) vs total congress (535)
    filing_members = (
        db.query(func.count(func.distinct(Trade.member_id))).scalar() or 0
    )

    # Buy/sell ratio
    buy_count = db.query(Trade).filter(Trade.transaction_type.ilike("%purchase%")).count()
    sell_count = db.query(Trade).filter(Trade.transaction_type.ilike("%sale%")).count()

    # Top 10 tickers by trade count (last 30 days)
    top_tickers = (
        db.query(
            Trade.ticker,
            func.count(Trade.id).label("trade_count"),
            func.sum(case((Trade.transaction_type.ilike("%purchase%"), 1), else_=0)).label("buys"),
            func.sum(case((Trade.transaction_type.ilike("%sale%"), 1), else_=0)).label("sells"),
            func.avg(Trade.amount_low).label("avg_amount"),
        )
        .filter(Trade.ticker.isnot(None), Trade.ticker != "")
        .filter(Trade.transaction_date >= thirty_days_ago)
        .group_by(Trade.ticker)
        .order_by(func.count(Trade.id).desc())
        .limit(10)
        .all()
    )

    # Most active members (last 30 days)
    top_members = (
        db.query(
            Member.name,
            Member.chamber,
            Member.party,
            func.count(Trade.id).label("trade_count"),
            func.sum(case((Trade.transaction_type.ilike("%purchase%"), 1), else_=0)).label("buys"),
            func.sum(case((Trade.transaction_type.ilike("%sale%"), 1), else_=0)).label("sells"),
        )
        .join(Trade)
        .filter(Trade.transaction_date >= thirty_days_ago)
        .group_by(Member.name, Member.chamber, Member.party)
        .order_by(func.count(Trade.id).desc())
        .limit(10)
        .all()
    )

    # Trades this week vs last week
    trades_this_week = db.query(Trade).filter(Trade.transaction_date >= seven_days_ago).count()
    trades_last_week = (
        db.query(Trade)
        .filter(Trade.transaction_date >= seven_days_ago - timedelta(days=7))
        .filter(Trade.transaction_date < seven_days_ago)
        .count()
    )

    return {
        "total_trades": total_trades,
        "total_members": total_members,
        "filing_members": filing_members,
        "congress_total": 535,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "trades_this_week": trades_this_week,
        "trades_last_week": trades_last_week,
        "top_tickers": [
            {
                "ticker": r.ticker,
                "trade_count": r.trade_count,
                "buys": r.buys or 0,
                "sells": r.sells or 0,
                "avg_amount": round(r.avg_amount) if r.avg_amount else None,
            }
            for r in top_tickers
        ],
        "top_members": [
            {
                "name": r.name,
                "chamber": r.chamber,
                "party": r.party or "N/A",
                "trade_count": r.trade_count,
                "buys": r.buys or 0,
                "sells": r.sells or 0,
            }
            for r in top_members
        ],
    }


@router.get("/leaderboard")
def leaderboard(
    period: str = "all",
    chamber: str | None = None,
    db: Session = Depends(get_db),
):
    """Politician leaderboard ranked by trade count and volume."""
    from datetime import timedelta

    query = (
        db.query(
            Member.name,
            Member.chamber,
            Member.party,
            Member.state,
            func.count(Trade.id).label("trade_count"),
            func.sum(case((Trade.transaction_type.ilike("%purchase%"), 1), else_=0)).label("buys"),
            func.sum(case((Trade.transaction_type.ilike("%sale%"), 1), else_=0)).label("sells"),
            func.sum(Trade.amount_low).label("total_volume"),
            func.max(Trade.transaction_date).label("last_trade_date"),
        )
        .join(Trade)
    )

    if period == "30d":
        query = query.filter(Trade.transaction_date >= datetime.utcnow() - timedelta(days=30))
    elif period == "90d":
        query = query.filter(Trade.transaction_date >= datetime.utcnow() - timedelta(days=90))
    elif period == "1y":
        query = query.filter(Trade.transaction_date >= datetime.utcnow() - timedelta(days=365))

    if chamber:
        query = query.filter(Member.chamber == chamber)

    rows = (
        query
        .group_by(Member.name, Member.chamber, Member.party, Member.state)
        .order_by(func.count(Trade.id).desc())
        .limit(50)
        .all()
    )

    return [
        {
            "rank": i + 1,
            "name": r.name,
            "chamber": r.chamber,
            "party": r.party or "N/A",
            "state": r.state or "N/A",
            "trade_count": r.trade_count,
            "buys": r.buys or 0,
            "sells": r.sells or 0,
            "total_volume": round(r.total_volume) if r.total_volume else 0,
            "last_trade_date": r.last_trade_date.isoformat() if r.last_trade_date else None,
        }
        for i, r in enumerate(rows)
    ]


@router.get("/members/{member_name}/profile")
def member_profile(member_name: str, db: Session = Depends(get_db)):
    """Individual politician profile with trade history and stats."""
    member = db.query(Member).filter(Member.name == member_name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    trades = (
        db.query(Trade)
        .filter(Trade.member_id == member.id)
        .order_by(Trade.transaction_date.desc().nullslast())
        .all()
    )

    # Stats
    buy_count = sum(1 for t in trades if t.transaction_type and "purchase" in t.transaction_type.lower())
    sell_count = sum(1 for t in trades if t.transaction_type and "sale" in t.transaction_type.lower())
    total_volume = sum(t.amount_low or 0 for t in trades)

    # Top tickers by count
    ticker_counts: dict[str, int] = {}
    for t in trades:
        if t.ticker:
            ticker_counts[t.ticker] = ticker_counts.get(t.ticker, 0) + 1
    top_tickers = sorted(ticker_counts.items(), key=lambda x: -x[1])[:10]

    # Sector breakdown
    sector_counts: dict[str, int] = {}
    for t in trades:
        s = t.sector or "Unknown"
        sector_counts[s] = sector_counts.get(s, 0) + 1
    sectors = sorted(sector_counts.items(), key=lambda x: -x[1])

    return {
        "name": member.name,
        "chamber": member.chamber,
        "party": member.party or "N/A",
        "state": member.state or "N/A",
        "total_trades": len(trades),
        "buys": buy_count,
        "sells": sell_count,
        "total_volume": round(total_volume),
        "top_tickers": [{"ticker": t, "count": c} for t, c in top_tickers],
        "sectors": [{"sector": s, "count": c} for s, c in sectors],
        "trades": [
            TradeOut(
                id=t.id,
                member_name=member.name,
                party=member.party,
                chamber=member.chamber,
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
            ).model_dump()
            for t in trades[:100]
        ],
    }


@router.get("/screener")
def stock_screener(
    period: str = "30d",
    min_trades: int = 2,
    db: Session = Depends(get_db),
):
    """Stock screener: most-traded tickers by congress with buy/sell signals."""
    from datetime import timedelta

    if period == "7d":
        cutoff = datetime.utcnow() - timedelta(days=7)
    elif period == "30d":
        cutoff = datetime.utcnow() - timedelta(days=30)
    elif period == "90d":
        cutoff = datetime.utcnow() - timedelta(days=90)
    elif period == "1y":
        cutoff = datetime.utcnow() - timedelta(days=365)
    else:
        cutoff = datetime.utcnow() - timedelta(days=30)

    rows = (
        db.query(
            Trade.ticker,
            Trade.sector,
            func.count(Trade.id).label("trade_count"),
            func.count(func.distinct(Trade.member_id)).label("unique_members"),
            func.sum(case((Trade.transaction_type.ilike("%purchase%"), 1), else_=0)).label("buys"),
            func.sum(case((Trade.transaction_type.ilike("%sale%"), 1), else_=0)).label("sells"),
            func.sum(Trade.amount_low).label("total_volume"),
            func.max(Trade.transaction_date).label("last_trade_date"),
        )
        .filter(Trade.ticker.isnot(None), Trade.ticker != "")
        .filter(Trade.transaction_date >= cutoff)
        .group_by(Trade.ticker, Trade.sector)
        .having(func.count(Trade.id) >= min_trades)
        .order_by(func.count(func.distinct(Trade.member_id)).desc(), func.count(Trade.id).desc())
        .limit(50)
        .all()
    )

    results = []
    for r in rows:
        buys = r.buys or 0
        sells = r.sells or 0
        members = r.unique_members or 1
        volume = r.total_volume or 0

        # Weighted signal score:
        # - Buy/sell ratio (base signal)
        # - Unique members multiplier (more members = stronger conviction)
        # - Volume factor (higher volume = stronger signal)
        if buys + sells == 0:
            score = 0
        else:
            ratio = (buys - sells) / (buys + sells)  # -1 to +1
            member_mult = min(members / 2.0, 3.0)  # Up to 3x for 6+ members
            vol_factor = 1.0 + min(volume / 500000.0, 2.0)  # Up to 3x for $500k+
            score = round(ratio * member_mult * vol_factor, 2)

        if score > 0.3:
            signal = "STRONG BUY"
        elif score > 0:
            signal = "BUY"
        elif score < -0.3:
            signal = "STRONG SELL"
        elif score < 0:
            signal = "SELL"
        else:
            signal = "MIXED"

        results.append({
            "ticker": r.ticker,
            "sector": r.sector or "N/A",
            "trade_count": r.trade_count,
            "unique_members": members,
            "buys": buys,
            "sells": sells,
            "signal": signal,
            "score": score,
            "total_volume": round(volume),
            "last_trade_date": r.last_trade_date.isoformat() if r.last_trade_date else None,
        })

    # Sort by absolute score (strongest signals first)
    results.sort(key=lambda x: abs(x["score"]), reverse=True)
    return results


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
        capitol_trades = loop.run_until_complete(scrape_capitol_trades(max_pages=400))
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
