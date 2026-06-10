"""FastAPI routes for the congressional trade tracker."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from sqlalchemy import case, func

from src.db.database import get_db
from src.db.models import Committee, Member, MemberCommittee, Subscriber, Trade
from src.services.committee_service import (
    check_committee_correlation,
    get_committee_members,
    get_committee_trades,
    get_member_committees,
)
from src.services.query_service import natural_language_query
from src.services.repeat_buyer_service import detect_repeat_buyers
from src.services.trade_service import search_trades

router = APIRouter(prefix="/api")

# Track last scrape time for health endpoint
_last_scrape_at: datetime | None = None
_last_scrape_new: int = 0


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


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Health check: last scrape time, trade counts, DB status."""
    from datetime import timedelta

    total_trades = db.query(Trade).count()
    total_members = db.query(Member).count()
    latest_by_filing = (
        db.query(Trade)
        .order_by(Trade.filing_date.desc().nullslast())
        .first()
    )
    latest_by_tx = (
        db.query(Trade)
        .order_by(Trade.transaction_date.desc().nullslast())
        .first()
    )
    latest_filing = latest_by_filing.filing_date.isoformat() if latest_by_filing and latest_by_filing.filing_date else None
    latest_tx = latest_by_tx.transaction_date.isoformat() if latest_by_tx and latest_by_tx.transaction_date else None

    # Trades added in last 24h
    yesterday = datetime.utcnow() - timedelta(hours=24)
    recent_ingested = db.query(Trade).filter(Trade.created_at >= yesterday).count() if hasattr(Trade, "created_at") else None

    return {
        "status": "ok",
        "db": "connected",
        "total_trades": total_trades,
        "total_members": total_members,
        "latest_filing_date": latest_filing,
        "latest_transaction_date": latest_tx,
        "last_scrape_at": _last_scrape_at.isoformat() if _last_scrape_at else None,
        "last_scrape_new_trades": _last_scrape_new,
        "scrape_schedule": "06:00 & 18:00 UTC + on deploy",
    }


@router.get("/trades", response_model=list[TradeOut])
def list_trades(
    member: str | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    transaction_type: str | None = None,
    owner: str | None = None,
    party: str | None = None,
    chamber: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else None
    dt = datetime.strptime(date_to, "%Y-%m-%d") if date_to else None

    trades = search_trades(
        db, member, ticker, sector, transaction_type, df, dt, limit, offset,
        owner=owner, party=party, chamber=chamber,
        min_amount=min_amount, max_amount=max_amount,
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


@router.get("/trades/recent", response_model=list[TradeOut])
def recent_trades(
    member: str | None = None,
    ticker: str | None = None,
    transaction_type: str | None = None,
    party: str | None = None,
    chamber: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get the most recent trades by transaction date with optional filters."""
    query = db.query(Trade).join(Member)
    if member:
        # Token-AND substring match so "Tim Walberg" finds "Timothy Walberg"
        tokens = [t for t in member.strip().split() if t]
        if len(tokens) == 1:
            query = query.filter(Member.name.ilike(f"%{tokens[0]}%"))
        else:
            for tok in tokens:
                query = query.filter(Member.name.ilike(f"%{tok}%"))
    if ticker:
        query = query.filter(Trade.ticker == ticker.upper())
    if transaction_type:
        query = query.filter(Trade.transaction_type.ilike(f"%{transaction_type}%"))
    if party:
        query = query.filter(Member.party.ilike(f"%{party}%"))
    if chamber:
        query = query.filter(Member.chamber == chamber)
    if min_amount is not None:
        query = query.filter(Trade.amount_high >= min_amount)
    if max_amount is not None:
        query = query.filter(Trade.amount_low <= max_amount)
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
    try:
        result = await natural_language_query(db, req.question)
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Query endpoint error: {e}")
        return {"error": f"Query failed: {str(e)}"}


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

    # Committee assignments
    committees = get_member_committees(db, member.id)

    # Committee-correlated trades
    correlated_trades = []
    for t in trades:
        correlation = check_committee_correlation(db, t)
        if correlation:
            correlated_trades.append({
                "ticker": t.ticker,
                "sector": t.sector,
                "transaction_type": t.transaction_type,
                "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                "amount_low": t.amount_low,
                "amount_high": t.amount_high,
                "matching_committees": correlation["matching_committees"],
            })

    # Repeat buys (same ticker purchased 2+ times in 180 days)
    repeat_buys: dict[str, int] = {}
    purchase_tickers = [
        t.ticker for t in trades
        if t.ticker and t.transaction_type and "purchase" in t.transaction_type.lower()
    ]
    for ticker in purchase_tickers:
        repeat_buys[ticker] = repeat_buys.get(ticker, 0) + 1
    repeat_buys = {k: v for k, v in repeat_buys.items() if v >= 2}

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
        "committees": committees,
        "committee_correlated_trades": correlated_trades,
        "repeat_buys": [
            {"ticker": t, "purchase_count": c}
            for t, c in sorted(repeat_buys.items(), key=lambda x: -x[1])
        ],
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


@router.get("/committees")
def list_committees(chamber: str | None = None, db: Session = Depends(get_db)):
    """List all committees with member counts."""
    query = db.query(Committee)
    if chamber:
        query = query.filter(Committee.chamber == chamber)
    committees = query.order_by(Committee.chamber, Committee.name).all()

    results = []
    for c in committees:
        member_count = (
            db.query(func.count(MemberCommittee.id))
            .filter(MemberCommittee.committee_id == c.id)
            .scalar()
        )
        results.append({
            "code": c.code,
            "name": c.name,
            "chamber": c.chamber,
            "member_count": member_count or 0,
        })
    return results


@router.get("/committees/{committee_code}")
def committee_detail(committee_code: str, db: Session = Depends(get_db)):
    """Get committee details with members and their trades."""
    committee = db.query(Committee).filter(Committee.code == committee_code).first()
    if not committee:
        raise HTTPException(status_code=404, detail="Committee not found")

    members = get_committee_members(db, committee_code)
    return {
        "code": committee.code,
        "name": committee.name,
        "chamber": committee.chamber,
        "members": members,
    }


@router.get("/committees/{committee_code}/trades")
def committee_trades_endpoint(
    committee_code: str,
    days: int = 90,
    db: Session = Depends(get_db),
):
    """Get trades by committee members, with sector-correlation flags.

    This is the Robert Latta signal — Energy & Commerce member buys Exxon.
    """
    committee = db.query(Committee).filter(Committee.code == committee_code).first()
    if not committee:
        raise HTTPException(status_code=404, detail="Committee not found")

    trades = get_committee_trades(db, committee_code, days=days)

    # Stats
    total = len(trades)
    correlated = sum(1 for t in trades if t["committee_correlated"])

    return {
        "committee_name": committee.name,
        "committee_code": committee.code,
        "period_days": days,
        "total_trades": total,
        "correlated_trades": correlated,
        "correlation_rate": round(correlated / total * 100, 1) if total else 0,
        "trades": trades,
    }


@router.get("/repeat-buyers")
def repeat_buyers(
    days: int = 180,
    min_purchases: int = 2,
    db: Session = Depends(get_db),
):
    """Find members who have bought the same stock multiple times.

    Conviction signal: repeat buying = high confidence in the trade.
    """
    return detect_repeat_buyers(db, lookback_days=days, min_purchases=min_purchases)


@router.post("/scrape-committees")
def trigger_committee_scrape(background_tasks: BackgroundTasks):
    """Scrape committee rosters and ingest assignments."""
    background_tasks.add_task(_run_committee_scrape)
    return {"message": "Committee scrape started in background."}


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


def _run_committee_scrape():
    """Scrape committee rosters and ingest into DB."""
    import asyncio
    import logging

    from src.db.database import SessionLocal
    from src.scrapers.committees import scrape_committees
    from src.services.committee_service import ingest_committees

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()
        raw = loop.run_until_complete(scrape_committees())
        loop.close()
        new_count = ingest_committees(db, raw)
        logger.info(f"Committee scrape complete: {new_count} new assignments")
    except Exception as e:
        logger.error(f"Committee scrape failed: {e}", exc_info=True)
    finally:
        db.close()


def _run_scrape(mode: str = "daily"):
    """Run scrape in background thread so it doesn't block the server.

    Modes:
      - "daily": Quick sync — Capitol Trades (10 pages) + all other sources
      - "backfill": Full historical — Capitol Trades (400 pages) + all sources
    """
    import asyncio
    import logging

    from src.db.database import SessionLocal
    from src.scrapers.capitol_trades import scrape_capitol_trades
    from src.scrapers.finnhub import scrape_finnhub_congress
    from src.scrapers.house import scrape_house_disclosures
    from src.scrapers.senate import scrape_senate_disclosures
    from src.scrapers.senate_efd import scrape_senate_efd_direct
    from src.scrapers.unusual_whales import scrape_unusual_whales
    from src.services.trade_service import ingest_trades

    logger = logging.getLogger(__name__)

    ct_pages = 400 if mode == "backfill" else 25
    logger.info(f"Background scrape started (mode={mode}, capitol_pages={ct_pages})")

    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()

        # Capitol Trades: main source for both chambers
        capitol_trades = loop.run_until_complete(scrape_capitol_trades(max_pages=ct_pages))
        logger.info(f"Capitol Trades: {len(capitol_trades)} trades scraped")

        # Senate: GitHub data repo (comprehensive Senate history)
        senate_trades = loop.run_until_complete(scrape_senate_disclosures())
        logger.info(f"Senate GitHub: {len(senate_trades)} trades scraped")

        # Senate eFD: direct from efdsearch.senate.gov (authoritative, freshest)
        efd_days = 180 if mode == "backfill" else 30
        senate_efd_trades = loop.run_until_complete(scrape_senate_efd_direct(days_back=efd_days))
        logger.info(f"Senate eFD direct: {len(senate_efd_trades)} trades scraped")

        # House: official clerk site (limited — no PDF parsing)
        house_trades = loop.run_until_complete(scrape_house_disclosures())
        logger.info(f"House Clerk: {len(house_trades)} trades scraped")

        # Finnhub: cross-reference with pagination
        finnhub_trades = loop.run_until_complete(scrape_finnhub_congress())
        logger.info(f"Finnhub: {len(finnhub_trades)} trades scraped")

        # Unusual Whales: prefer the authenticated API when UW_API_TOKEN is set
        # (no pagination cap, more reliable). Fall back to the public-page
        # scraper when no token is configured.
        import os as _os
        if _os.environ.get("UW_API_TOKEN", "").strip():
            from src.scrapers.uw_api import scrape_uw_api
            uw_max = 40 if mode == "backfill" else 20
            uw_trades = loop.run_until_complete(scrape_uw_api(max_pages=uw_max))
            logger.info(f"Unusual Whales API: {len(uw_trades)} trades scraped")
        else:
            uw_pages = 50 if mode == "backfill" else 15
            uw_trades = loop.run_until_complete(scrape_unusual_whales(max_pages=uw_pages))
            logger.info(f"Unusual Whales (page scrape): {len(uw_trades)} trades scraped")

        loop.close()

        all_trades = (
            capitol_trades + senate_trades + senate_efd_trades
            + house_trades + finnhub_trades + uw_trades
        )
        new_count = ingest_trades(db, all_trades)

        # Update health tracking
        global _last_scrape_at, _last_scrape_new
        _last_scrape_at = datetime.utcnow()
        _last_scrape_new = new_count

        logger.info(
            f"Background scrape complete ({mode}): {new_count} new trades "
            f"(capitol={len(capitol_trades)}, senate={len(senate_trades)}, "
            f"senate_efd={len(senate_efd_trades)}, house={len(house_trades)}, "
            f"finnhub={len(finnhub_trades)}, uw={len(uw_trades)})"
        )
    except Exception as e:
        logger.error(f"Background scrape failed: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/scrape")
def trigger_scrape(background_tasks: BackgroundTasks):
    """Quick daily sync — Capitol Trades (10 pages) + all other sources."""
    background_tasks.add_task(_run_scrape, "daily")
    return {"message": "Daily scrape started in background. Check logs for progress."}


@router.get("/admin/cross-source-audit")
async def cross_source_audit(min_gap: int = 5, db: Session = Depends(get_db)):
    """Compare our per-politician trade counts to Unusual Whales' counts.

    Returns the list of politicians where UW shows materially more trades than
    we have on file. Run daily — anything that shows up is a coverage gap.

    Args:
      min_gap: only flag politicians where (uw_count - our_count) >= this.
    """
    import os
    import httpx
    import re
    import json as _json
    from src.db.models import Member
    from src.services.trade_service import normalize_member_name

    uw_politicians = []
    # Prefer the authenticated API roster (ground truth) when a token exists.
    if os.environ.get("UW_API_TOKEN", "").strip():
        from src.scrapers.uw_api import fetch_politicians
        api_pols = await fetch_politicians()
        # Normalize API shape to match the page-scrape shape used below
        for p in api_pols:
            uw_politicians.append({
                "full_name": p.get("name") or p.get("full_name"),
                "total_trades": p.get("trade_count") or p.get("total_trades") or 0,
                "current_chamber": p.get("chamber") or p.get("current_chamber"),
                "current_district": p.get("district") or p.get("current_district") or "",
                "id": p.get("politician_id") or p.get("id"),
                "name_slug": p.get("name_slug"),
            })

    # Fall back to the public /politics page if API unavailable / empty
    if not uw_politicians:
        async with httpx.AsyncClient(
            timeout=45,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            resp = await client.get("https://unusualwhales.com/politics")
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
                resp.text,
                re.DOTALL,
            )
            if not m:
                return {"error": "Could not fetch UW politician_data"}
            data = _json.loads(m.group(1))
            uw_politicians = (
                data.get("props", {}).get("pageProps", {}).get("politician_data", [])
            )

    # Build OUR per-member counts
    our_counts: dict[int, int] = {}
    members = db.query(Member).all()
    for m in members:
        our_counts[m.id] = (
            db.query(Trade).filter(Trade.member_id == m.id).count()
        )

    def _last_name(s: str) -> str:
        toks = [
            t for t in s.replace(",", "").split()
            if t.lower() not in {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
        ]
        return toks[-1].lower() if toks else ""

    def _first_letter(s: str) -> str:
        toks = s.split()
        return toks[0][0].lower() if toks and toks[0] else ""

    # Match each UW politician to our member by (last_name, chamber, state, first letter)
    gaps = []
    for uw in uw_politicians:
        uw_name = uw.get("full_name") or ""
        uw_count = uw.get("total_trades") or 0
        uw_chamber = (uw.get("current_chamber") or "").lower()
        uw_district = uw.get("current_district") or ""
        uw_state = uw_district.split("-")[0].upper() if uw_district else None

        if not uw_name or not uw_chamber:
            continue
        uw_chamber_norm = "House" if uw_chamber == "house" else "Senate"

        # Find matching member by (last, chamber, state) + first letter
        uw_last = _last_name(uw_name)
        uw_first_letter = _first_letter(uw_name)

        match = None
        for our_m in members:
            if our_m.chamber != uw_chamber_norm:
                continue
            if _last_name(our_m.name) != uw_last:
                continue
            # If both have state, require match
            if uw_state and our_m.state and our_m.state != uw_state:
                continue
            # First letter must match (avoids Greg Stanton vs Greg Steube collisions)
            if _first_letter(our_m.name) != uw_first_letter:
                continue
            match = our_m
            break

        our_count = our_counts.get(match.id, 0) if match else 0
        gap = uw_count - our_count

        if gap >= min_gap:
            gaps.append({
                "uw_name": uw_name,
                "our_name": match.name if match else None,
                "chamber": uw_chamber_norm,
                "state": uw_state,
                "district": uw_district,
                "uw_count": uw_count,
                "our_count": our_count,
                "gap": gap,
                "uw_politician_id": uw.get("id"),
                "name_slug": uw.get("name_slug"),
            })

    gaps.sort(key=lambda x: x["gap"], reverse=True)
    return {
        "checked_uw_politicians": len(uw_politicians),
        "our_members": len(members),
        "gaps_found": len(gaps),
        "total_missing_trades": sum(g["gap"] for g in gaps),
        "min_gap_threshold": min_gap,
        "gaps": gaps[:100],  # cap response size; full list in DB anyway
    }


@router.post("/admin/backfill-discrepancies")
def backfill_discrepancies(background_tasks: BackgroundTasks):
    """For each politician with a coverage gap vs UW, deep-scrape them.
    Runs the audit, then for each gap, scrapes their UW pages and ingests.
    """
    background_tasks.add_task(_run_backfill_discrepancies)
    return {"message": "Discrepancy backfill started."}


def _run_backfill_discrepancies():
    """Audit ours vs UW, then deep-scrape any politician with a gap via
    Capitol Trades' per-politician URL (the only source where bioguide-id
    filtering actually works to return per-member full history).
    """
    import asyncio
    import logging
    import re
    import json as _json
    import httpx

    from src.db.database import SessionLocal
    from src.db.models import Member
    from src.scrapers.capitol_trades import _scrape_politician_trades
    from src.services.trade_service import ingest_trades, normalize_member_name

    logger = logging.getLogger(__name__)

    async def run():
        async with httpx.AsyncClient(
            timeout=45,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            # 1. Get UW politician_data with total_trades counts
            resp = await client.get("https://unusualwhales.com/politics")
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
                resp.text,
                re.DOTALL,
            )
            data = _json.loads(m.group(1))
            uw_politicians = (
                data.get("props", {}).get("pageProps", {}).get("politician_data", [])
            )

            # 2. Load our member counts
            db = SessionLocal()
            try:
                our_members = db.query(Member).all()
                from src.db.models import Trade
                our_counts = {
                    m.id: db.query(Trade).filter(Trade.member_id == m.id).count()
                    for m in our_members
                }
            finally:
                db.close()

            def _last(s: str) -> str:
                toks = [t for t in s.replace(",", "").split()
                        if t.lower() not in {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}]
                return toks[-1].lower() if toks else ""
            def _fl(s: str) -> str:
                toks = s.split()
                return toks[0][0].lower() if toks and toks[0] else ""

            # 3. For each UW politician with gap > 5, try to find their
            #    bioguide_id by scraping Capitol Trades' politicians page once.
            #    The Capitol Trades politicians page lists bioguide IDs we
            #    can then use for per-politician deep scrape.
            from src.scrapers.capitol_trades import _get_top_politician_ids
            ct_pols = await _get_top_politician_ids(client)
            # Index by last name for quick lookup
            ct_by_lastname: dict[str, list[tuple[str, str]]] = {}
            for bio_id, name in ct_pols:
                ct_by_lastname.setdefault(_last(name), []).append((bio_id, name))

            backfilled = 0
            total_new = 0
            for uw in uw_politicians:
                uw_total = uw.get("total_trades") or 0
                uw_name = uw.get("full_name") or ""
                uw_chamber = (uw.get("current_chamber") or "").lower()
                uw_district = uw.get("current_district") or ""
                uw_state = uw_district.split("-")[0].upper() if uw_district else None
                if not uw_name or uw_total < 1:
                    continue

                uw_last = _last(uw_name)
                uw_fl = _fl(uw_name)
                chamber_norm = "House" if uw_chamber == "house" else "Senate"

                # Find matching member
                match_id = None
                for our_m in our_members:
                    if our_m.chamber != chamber_norm:
                        continue
                    if _last(our_m.name) != uw_last:
                        continue
                    if uw_state and our_m.state and our_m.state != uw_state:
                        continue
                    if _fl(our_m.name) != uw_fl:
                        continue
                    match_id = our_m.id
                    break
                our_n = our_counts.get(match_id, 0) if match_id else 0
                gap = uw_total - our_n
                if gap < 5:
                    continue

                # Find this politician's Capitol Trades bioguide id.
                # Step 1: check the (shallow) CT roster we already loaded.
                ct_candidates = ct_by_lastname.get(uw_last, [])
                bio_id = None
                for bid, cname in ct_candidates:
                    if _fl(cname) == uw_fl:
                        bio_id = bid
                        break
                # Step 2: if not in the roster (most heavy traders aren't),
                # search Capitol Trades by last name to resolve the bioguide.
                if not bio_id:
                    try:
                        import re as _re
                        sr = await client.get(
                            f"https://www.capitoltrades.com/politicians?search={uw_last}"
                        )
                        found = list(dict.fromkeys(_re.findall(r'/politicians/([A-Z]\d+)', sr.text)))
                        if len(found) == 1:
                            bio_id = found[0]
                        elif len(found) > 1:
                            # Multiple same-lastname — match by first initial in the
                            # rendered names near each id is unreliable; take the first
                            # whose page name shares the first letter.
                            for cand in found:
                                if uw_fl and uw_fl in sr.text.lower():
                                    bio_id = cand
                                    break
                            bio_id = bio_id or found[0]
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.debug(f"CT search failed for {uw_name}: {e}")
                if not bio_id:
                    logger.info(f"No CT bioguide for {uw_name} (gap={gap})")
                    continue

                # Deep-scrape via Capitol Trades
                try:
                    trades = await _scrape_politician_trades(client, bio_id, 96)
                    if trades:
                        db = SessionLocal()
                        try:
                            new = ingest_trades(db, trades)
                            total_new += new
                            backfilled += 1
                            logger.info(
                                f"Backfill {uw_name} (gap was {gap}): scraped {len(trades)}, "
                                f"+{new} new (running total +{total_new})"
                            )
                        finally:
                            db.close()
                    await asyncio.sleep(0.4)
                except Exception as e:
                    logger.error(f"Backfill {uw_name} failed: {e}")

            logger.info(
                f"Discrepancy backfill complete: backfilled {backfilled} politicians, +{total_new} trades"
            )

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(run())
        loop.close()
    except Exception as e:
        logger.error(f"Backfill outer failure: {e}", exc_info=True)

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(run())
        loop.close()
    except Exception as e:
        logger.error(f"Backfill outer failure: {e}", exc_info=True)


@router.get("/admin/env-check")
def env_check():
    """Diagnostic: report which expected env vars are visible to the running
    process. Returns presence + length only — never the secret values.
    """
    import os
    watch = [
        "UW_API_TOKEN", "SLACK_WEBHOOK_URL", "ANTHROPIC_API_KEY",
        "DATABASE_URL", "RESEND_API_KEY", "FINNHUB_API_KEY",
        "EMAIL_FROM", "EMAIL_HOUR", "SCRAPE_INTERVAL_HOURS",
    ]
    out = {}
    for k in watch:
        v = os.environ.get(k)
        out[k] = {"present": v is not None, "length": len(v) if v else 0}
    # Also report Railway environment identifiers if present
    rail = {
        k: os.environ.get(k)
        for k in ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_SERVICE_NAME", "RAILWAY_PROJECT_NAME")
    }
    return {"vars": out, "railway": rail}


@router.get("/admin/uw-debug")
async def uw_debug():
    """Raw diagnostic for the UW unusual-trades endpoint — surfaces HTTP status
    and the raw top-level response shape so we can see why anomaly-feed is empty
    (tier restriction? different response key? genuinely no data?).
    """
    import os
    import httpx
    token = os.environ.get("UW_API_TOKEN", "").strip()
    if not token:
        return {"error": "UW_API_TOKEN not set"}
    out = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for path in ["/congress/unusual-trades", "/congress/unusual-trades/stats", "/congress/late-reports"]:
            try:
                r = await client.get(
                    f"https://api.unusualwhales.com/api{path}",
                    params={"limit": 5},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
                body = r.text[:600]
                shape = None
                try:
                    j = r.json()
                    if isinstance(j, dict):
                        shape = {k: (f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__) for k, v in j.items()}
                    elif isinstance(j, list):
                        shape = f"list[{len(j)}]"
                except Exception:
                    pass
                out[path] = {"status": r.status_code, "shape": shape, "raw_preview": body}
            except Exception as e:
                out[path] = {"error": str(e)}
    return out


@router.get("/anomaly-feed")
async def anomaly_feed(types: str | None = None, limit: int = 200):
    """Congressional trades flagged as unusual by Unusual Whales, with reason tags.

    Tags: committee_conflict, first_person_to_trade, low_marketcap,
    unusual_industry, unusually_large_trade, fec_donation_conflict.
    Requires UW_API_TOKEN. This is the alpha-signal feed (API-only — not
    available from scraping).
    """
    import os
    import httpx
    token = os.environ.get("UW_API_TOKEN", "").strip()
    if not token:
        return {"error": "UW_API_TOKEN not set", "trades": []}
    # Direct call so we can detect the premium-tier gate (HTTP 422 with a
    # "premium endpoint" message) and report it cleanly to the UI.
    params = {"limit": min(limit, 500)}
    if types:
        params["types"] = types
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://api.unusualwhales.com/api/congress/unusual-trades",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if r.status_code == 422 and "premium" in r.text.lower():
        return {
            "premium_required": True,
            "message": "The Anomaly Feed requires the Unusual Whales premium API tier. "
                       "Contact dev@unusualwhales.com or upgrade your plan to enable it.",
            "trades": [],
        }
    if r.status_code != 200:
        return {"error": f"UW API returned {r.status_code}", "trades": []}
    rows = r.json().get("data", []) if r.headers.get("content-type", "").startswith("application/json") else []
    return {"count": len(rows), "types_filter": types, "trades": rows[:limit]}


@router.get("/late-filers")
async def late_filers(limit: int = 200):
    """Politicians late on their STOCK Act PTR filings (UW API). Insider-signal."""
    import os
    if not os.environ.get("UW_API_TOKEN", "").strip():
        return {"error": "UW_API_TOKEN not set", "late": []}
    from src.scrapers.uw_api import fetch_late_reports
    rows = await fetch_late_reports(limit=limit)
    return {"count": len(rows), "late": rows}


@router.post("/admin/slack-daily-summary")
async def slack_daily_summary(db: Session = Depends(get_db)):
    """Run a fresh audit + health check and POST a formatted summary to Slack.

    Webhook URL is read from env var SLACK_WEBHOOK_URL (set in Railway).
    Returns the message body so we can inspect what was sent without checking Slack.

    Wire this into the end of the daily routines — one call replaces the
    audit + cross-source + health + format chain in the remote-trigger prompt.
    """
    import os
    import httpx
    from datetime import datetime as _dt

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return {
            "error": "SLACK_WEBHOOK_URL not set in Railway environment",
            "fix": "Add it under Railway → service → Variables, then redeploy."
        }

    # 1. Member audit (read-only)
    audit = _member_audit(db)
    # 2. Cross-source audit
    cross = await cross_source_audit(min_gap=5, db=db)
    # 3. Health
    from src.db.models import Trade, Member
    total_trades = db.query(Trade).count()
    total_members = db.query(Member).count()
    latest_filing = (
        db.query(Trade).order_by(Trade.filing_date.desc().nullslast()).first()
    )
    latest_tx = (
        db.query(Trade).order_by(Trade.transaction_date.desc().nullslast()).first()
    )
    lf = latest_filing.filing_date.strftime("%Y-%m-%d") if latest_filing and latest_filing.filing_date else "?"
    ltx = latest_tx.transaction_date.strftime("%Y-%m-%d") if latest_tx and latest_tx.transaction_date else "?"

    today_str = _dt.utcnow().strftime("%Y-%m-%d")
    name_clean = audit.get("is_clean", False)
    uw_gaps = cross.get("gaps_found", 0)
    missing = cross.get("total_missing_trades", 0)

    # Build status emoji and label
    if not name_clean and uw_gaps >= 10:
        status = ":rotating_light: ALERT"
    elif uw_gaps >= 5 or not name_clean:
        status = ":warning: Warning"
    else:
        status = ":white_check_mark: All Clean"

    # Build the Slack message — Block Kit for readability
    header_line = (
        f"*{status} — Sub-Nancy Daily Audit · {today_str}*"
    )
    summary_fields = [
        {"type": "mrkdwn", "text": f"*Total Trades:*\n{total_trades:,}"},
        {"type": "mrkdwn", "text": f"*Members Tracked:*\n{total_members:,}"},
        {"type": "mrkdwn", "text": f"*Latest Filing:*\n{lf}"},
        {"type": "mrkdwn", "text": f"*Latest Transaction:*\n{ltx}"},
        {"type": "mrkdwn", "text": f"*UW Coverage Gaps:*\n{uw_gaps} politicians"},
        {"type": "mrkdwn", "text": f"*Missing Trades:*\n{missing:,}"},
    ]

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_line}},
        {"type": "section", "fields": summary_fields},
    ]

    # Top 5 gaps if any
    gaps_list = cross.get("gaps", [])[:5]
    if gaps_list:
        gap_lines = []
        for g in gaps_list:
            gap_lines.append(
                f"• *{g['uw_name']}* ({g.get('chamber','?')} {g.get('state') or ''}) — "
                f"UW: {g['uw_count']} / Ours: {g['our_count']} → missing {g['gap']}"
            )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Top Coverage Gaps:*\n" + "\n".join(gap_lines)}
        })

    # Name dirtiness details
    if not name_clean:
        bad = []
        if audit.get("dirty_names"):
            bad.append(f"• {len(audit['dirty_names'])} dirty names (e.g. {audit['dirty_names'][:2]})")
        dgroups = audit.get("duplicate_groups") or {}
        if dgroups:
            sample = list(dgroups.values())[:3]
            bad.append(f"• {len(dgroups)} duplicate groups (e.g. {sample})")
        if bad:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Name Cleanup Needed:*\n" + "\n".join(bad)}
            })

    # Link to dashboard
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "<https://sub-nancy-production.up.railway.app/|Open Sub-Nancy Dashboard> · <https://sub-nancy-production.up.railway.app/|Coverage Audit Tab>"}]
    })

    payload = {
        "text": f"{status} Sub-Nancy Audit {today_str}: {total_trades:,} trades, {uw_gaps} gaps",
        "blocks": blocks,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(webhook_url, json=payload)
            r.raise_for_status()
    except httpx.HTTPError as e:
        return {"error": f"Slack POST failed: {e}", "payload": payload}

    return {
        "sent": True,
        "status_label": status,
        "total_trades": total_trades,
        "uw_gaps": uw_gaps,
        "missing_trades": missing,
        "name_clean": name_clean,
        "payload": payload,
    }


def _member_audit(db: Session) -> dict:
    """Member audit using same_person() detection — shared by the audit
    endpoint and the Slack summary.
    """
    from src.db.models import Member
    from src.services.trade_service import same_person

    members = db.query(Member).all()

    # Dirty = still carries honorific/comma format and needs normalizing
    dirty = [m.name for m in members if "Hon" in m.name or "," in m.name]

    # True duplicates = same_person() AND state-compatible. This correctly
    # MERGES "Tammy Duckworth"/"Ladda Tammy Duckworth" while KEEPING
    # "Austin Scott"/"David Scott" (same last name, different people).
    true_dupes = {}
    used = set()
    for i, a in enumerate(members):
        if a.id in used:
            continue
        group = [a]
        for b in members[i + 1:]:
            if b.id in used:
                continue
            if a.chamber == b.chamber and same_person(a.name, b.name):
                if not a.state or not b.state or a.state == b.state:
                    group.append(b)
                    used.add(b.id)
        if len(group) > 1:
            true_dupes[f"{a.name} ({a.chamber})"] = [m.name for m in group]

    return {
        "is_clean": not dirty and not true_dupes,
        "total_members": len(members),
        "dirty_names": dirty,
        "duplicate_groups": true_dupes,
    }


@router.get("/admin/backfill-one")
async def backfill_one(name: str, max_pages: int = 200, db: Session = Depends(get_db)):
    """Synchronously deep-scrape ONE politician via Capitol Trades and ingest.

    Runs in-request (not a background task) because FastAPI BackgroundTasks
    don't reliably complete long async work on this Railway deploy — the
    proven-reliable pattern is synchronous execution (same as the Bresnahan
    debug endpoint that worked). Resolve the bioguide via CT name search,
    deep-scrape, ingest, return the count. Call this in a loop over the gap list.
    """
    import re as _re
    import httpx
    from src.scrapers.capitol_trades import _scrape_politician_trades
    from src.services.trade_service import ingest_trades

    last = name.strip().split()[-1] if name.strip() else ""
    if not last:
        return {"error": "no name"}

    async with httpx.AsyncClient(
        timeout=60, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    ) as client:
        # Resolve bioguide via CT search
        try:
            sr = await client.get(f"https://www.capitoltrades.com/politicians?search={last}")
            ids = list(dict.fromkeys(_re.findall(r'/politicians/([A-Z]\d+)', sr.text)))
        except Exception as e:
            return {"error": f"search failed: {e}"}
        if not ids:
            return {"name": name, "bioguide": None, "scraped": 0, "new": 0, "note": "not found on CT"}
        bio_id = ids[0]
        trades = await _scrape_politician_trades(client, bio_id, 96, max_pages=max_pages)

    new = ingest_trades(db, trades) if trades else 0
    return {"name": name, "bioguide": bio_id, "scraped": len(trades), "new": new}


@router.post("/admin/dedupe-smart")
def dedupe_smart(db: Session = Depends(get_db)):
    """Definitive dedupe using same_person() — merges same-person/different-spelling
    rows (Ladda Tammy/Tammy Duckworth, Tim/Timothy Walberg) while keeping genuinely
    different people who share a last name (Austin Scott vs David Scott).
    """
    from src.db.models import Member, MemberCommittee
    from src.services.trade_service import same_person, normalize_member_name

    members = db.query(Member).all()
    # Bucket by (chamber) then greedily group by same_person
    groups: list[list[Member]] = []
    for m in members:
        placed = False
        for g in groups:
            if g[0].chamber == m.chamber and same_person(g[0].name, m.name):
                # also require state compatibility (one side null OR equal)
                if not g[0].state or not m.state or g[0].state == m.state:
                    g.append(m)
                    placed = True
                    break
        if not placed:
            groups.append([m])

    merged = trades_moved = deleted = 0
    actions = []
    for g in groups:
        if len(g) < 2:
            continue
        # keeper: most trades, then has party, then longest (most formal) name
        g.sort(key=lambda m: (
            db.query(Trade).filter(Trade.member_id == m.id).count(),
            1 if m.party else 0,
            len(m.name),
        ), reverse=True)
        keeper = g[0]
        for d in g[1:]:
            for f in ("party", "state", "district"):
                if not getattr(keeper, f) and getattr(d, f):
                    setattr(keeper, f, getattr(d, f))
            n = db.query(Trade).filter(Trade.member_id == d.id).update({"member_id": keeper.id})
            db.query(MemberCommittee).filter(MemberCommittee.member_id == d.id).update({"member_id": keeper.id})
            trades_moved += n
            actions.append({"keeper": keeper.name, "merged": d.name, "trades_moved": n})
            db.delete(d)
            deleted += 1
        keeper.name = normalize_member_name(keeper.name)
        merged += 1

    db.commit()
    return {
        "groups_merged": merged,
        "rows_deleted": deleted,
        "trades_reassigned": trades_moved,
        "actions": actions[:50],
        "total_actions": len(actions),
    }


@router.get("/admin/audit-members")
def audit_members(db: Session = Depends(get_db)):
    """Read-only audit of the member table using same_person() detection.
    Returns is_clean=true when there are no dirty names and no true duplicates.
    """
    return _member_audit(db)


@router.post("/admin/normalize-member-names")
def normalize_member_names(db: Session = Depends(get_db)):
    """Rewrite every member's stored name through normalize_member_name.

    Turns "Bresnahan, Hon.. Rob" into "Rob Bresnahan", strips honorifics,
    flips comma-format to First-Last, drops middle initials. After this
    runs, the member dropdown is clean.

    If normalization collides with an existing member, merges them (moves
    trades and committee links to the existing keeper, deletes the dupe).
    """
    from src.db.models import Member, MemberCommittee
    from src.services.trade_service import normalize_member_name

    renamed = 0
    merged = 0
    trades_moved = 0
    committees_moved = 0
    actions = []

    members = db.query(Member).all()
    by_key: dict[tuple[str, str], Member] = {}

    # Pre-populate the lookup with members that already have clean names
    for m in members:
        canon = normalize_member_name(m.name)
        key = (canon, m.chamber)
        if key not in by_key:
            by_key[key] = m

    for m in members:
        canon = normalize_member_name(m.name)
        if not canon:
            continue
        key = (canon, m.chamber)
        keeper = by_key[key]

        if keeper.id == m.id:
            # This one is the keeper — just update its name if needed
            if m.name != canon:
                actions.append({"renamed": m.name, "to": canon})
                m.name = canon
                renamed += 1
        else:
            # Found a duplicate — merge into keeper
            for field in ("party", "state", "district"):
                if not getattr(keeper, field) and getattr(m, field):
                    setattr(keeper, field, getattr(m, field))
            n_trades = (
                db.query(Trade).filter(Trade.member_id == m.id).update({"member_id": keeper.id})
            )
            n_comm = (
                db.query(MemberCommittee).filter(MemberCommittee.member_id == m.id).update({"member_id": keeper.id})
            )
            trades_moved += n_trades
            committees_moved += n_comm
            actions.append({"merged": m.name, "into": keeper.name, "trades_moved": n_trades})
            db.delete(m)
            merged += 1

    db.commit()
    return {
        "renamed": renamed,
        "merged": merged,
        "trades_reassigned": trades_moved,
        "committee_links_reassigned": committees_moved,
        "actions": actions[:50],
        "total_actions": len(actions),
    }


@router.post("/admin/dedupe-members-by-lastname-state")
def dedupe_members_by_lastname_state(db: Session = Depends(get_db)):
    """Second-pass dedupe: when 2+ members share the same last name, chamber,
    AND state, they're the same person regardless of first-name spelling.

    Catches the cases the first dedupe couldn't:
      - Rob Bresnahan vs Robert Bresnahan (nickname)
      - Jack Reed vs John F Reed
      - Rick Scott vs Richard Scott
      - Tammy Duckworth vs Ladda Tammy Duckworth
    """
    from collections import defaultdict
    from src.db.models import Member, MemberCommittee
    from src.services.trade_service import normalize_member_name

    def last_name_key(name: str) -> str:
        """Extract a comparable last-name token from a (possibly messy) name."""
        # First normalize (flips commas, drops honorifics)
        n = normalize_member_name(name)
        tokens = [t for t in n.split() if t.lower() not in {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv"}]
        return tokens[-1].lower() if tokens else ""

    def first_name_key(name: str) -> str:
        n = normalize_member_name(name)
        tokens = n.split()
        return tokens[0].lower() if tokens else ""

    def first_compatible(a: str, b: str) -> bool:
        """Treat Tim/Timothy, Susie/Suzanne, etc. as the same first name when
        the outer match has already constrained on (last_name, chamber, state).
        """
        a, b = a.lower(), b.lower()
        if not a or not b:
            return True
        if a == b:
            return True
        if a.startswith(b) or b.startswith(a):
            return True
        if len(a) >= 3 and len(b) >= 3 and a[:3] == b[:3]:
            return True
        # Same first letter is enough given the outer (last_name, chamber, state) match
        if a[0] == b[0]:
            return True
        return False

    # Group by (last_name, chamber, state). If state is missing on a row, we
    # can still match it provided exactly one OTHER row in the same
    # (last_name, chamber) bucket has a state set — we'll merge into that one.
    groups: dict[tuple[str, str, str | None], list[Member]] = defaultdict(list)
    for m in db.query(Member).all():
        key = (last_name_key(m.name), m.chamber, m.state)
        groups[key].append(m)

    # Also build a parallel index by (lastname, chamber) ignoring state to
    # handle rows that have null state.
    by_last_chamber: dict[tuple[str, str], list[Member]] = defaultdict(list)
    for m in db.query(Member).all():
        by_last_chamber[(last_name_key(m.name), m.chamber)].append(m)

    merged = 0
    trades_moved = 0
    committees_moved = 0
    deleted = 0
    actions = []

    # First pass: merge rows that match on (last, chamber, state) — high confidence.
    # Within a (last, chamber, state) group, also collapse first-name variants
    # like Tim/Timothy and Josh/Joshua via first_compatible.
    handled_ids: set[int] = set()
    expanded_groups: list[list[Member]] = []
    for (last, chamber, state), members in list(groups.items()):
        if len(members) <= 1 or not last:
            continue
        # Bucket members by mutually-compatible first names
        buckets: list[list[Member]] = []
        for m in members:
            placed = False
            for b in buckets:
                if first_compatible(first_name_key(b[0].name), first_name_key(m.name)):
                    b.append(m)
                    placed = True
                    break
            if not placed:
                buckets.append([m])
        for b in buckets:
            if len(b) > 1:
                expanded_groups.append(b)

    for members in expanded_groups:
        members.sort(key=lambda m: (1 if m.party else 0,
                                    db.query(Trade).filter(Trade.member_id == m.id).count(),
                                    0 if ("," in m.name or "Hon" in m.name) else 1,
                                    # Prefer the longer (more formal) first name as canonical
                                    len(m.name)),
                     reverse=True)
        keeper = members[0]
        dups = members[1:]
        state = keeper.state
        for d in dups:
            handled_ids.add(d.id)
            for field in ("party", "state", "district"):
                if not getattr(keeper, field) and getattr(d, field):
                    setattr(keeper, field, getattr(d, field))
            n_trades = (
                db.query(Trade).filter(Trade.member_id == d.id).update({"member_id": keeper.id})
            )
            n_comm = (
                db.query(MemberCommittee).filter(MemberCommittee.member_id == d.id).update({"member_id": keeper.id})
            )
            trades_moved += n_trades
            committees_moved += n_comm
            actions.append({
                "keeper": keeper.name,
                "merged": d.name,
                "state": state,
                "trades_moved": n_trades,
            })
            db.delete(d)
            deleted += 1
        # Canonicalize keeper's name
        keeper.name = normalize_member_name(keeper.name)
        merged += 1

    # Second pass: merge by (last, chamber) when one row has null state.
    # Now also gated by first-name compatibility so Tim/Timothy and Josh/Joshua
    # collapse even when one row's state is null.
    for (last, chamber), members in by_last_chamber.items():
        members = [m for m in members if m.id not in handled_ids]
        if len(members) <= 1 or not last:
            continue
        with_state = [m for m in members if m.state]
        without_state = [m for m in members if not m.state]
        if len(with_state) == 1 and without_state:
            keeper = with_state[0]
            kf = first_name_key(keeper.name)
            for d in without_state:
                if d.id in handled_ids:
                    continue
                if not first_compatible(kf, first_name_key(d.name)):
                    continue
                handled_ids.add(d.id)
                for field in ("party", "district"):
                    if not getattr(keeper, field) and getattr(d, field):
                        setattr(keeper, field, getattr(d, field))
                n_trades = (
                    db.query(Trade).filter(Trade.member_id == d.id).update({"member_id": keeper.id})
                )
                n_comm = (
                    db.query(MemberCommittee).filter(MemberCommittee.member_id == d.id).update({"member_id": keeper.id})
                )
                trades_moved += n_trades
                committees_moved += n_comm
                actions.append({
                    "keeper": keeper.name,
                    "merged": d.name,
                    "state": "(via lastname-only match)",
                    "trades_moved": n_trades,
                })
                db.delete(d)
                deleted += 1
            keeper.name = normalize_member_name(keeper.name)
            merged += 1

    db.commit()
    return {
        "groups_merged": merged,
        "duplicate_rows_deleted": deleted,
        "trades_reassigned": trades_moved,
        "committee_links_reassigned": committees_moved,
        "actions": actions[:40],
        "total_actions": len(actions),
    }


@router.post("/admin/dedupe-members")
def dedupe_members(db: Session = Depends(get_db)):
    """Merge duplicate Member rows that refer to the same person under different
    name spellings. Reassigns all trades and committee links to the canonical
    member row, then deletes the duplicates.

    Picks the canonical row by preferring:
      1. Whichever has a non-null `party` set
      2. Whichever has the most trades attached
      3. Whichever name normalizes "cleanest" (no commas, no Hon..)
    """
    from collections import defaultdict
    from src.db.models import Member, MemberCommittee
    from src.services.trade_service import normalize_member_name

    # Group members by (canonical_name, chamber)
    groups: dict[tuple[str, str], list[Member]] = defaultdict(list)
    for m in db.query(Member).all():
        key = (normalize_member_name(m.name), m.chamber)
        groups[key].append(m)

    merged = 0
    trades_moved = 0
    committees_moved = 0
    deleted = 0
    actions = []

    for (canonical_name, chamber), members in groups.items():
        if len(members) <= 1:
            continue

        # Pick canonical row: party set + most trades wins
        def score(m: Member) -> tuple:
            trade_count = db.query(Trade).filter(Trade.member_id == m.id).count()
            has_party = 1 if m.party else 0
            # Prefer name without "Hon" or commas (rough cleanliness signal)
            clean = 0 if ("," in m.name or "Hon" in m.name) else 1
            return (has_party, trade_count, clean)

        members.sort(key=score, reverse=True)
        keeper = members[0]
        dups = members[1:]

        # Normalize keeper's stored name
        if keeper.name != canonical_name:
            keeper.name = canonical_name
        # Backfill missing fields from dupes
        for d in dups:
            for field in ("party", "state", "district"):
                if not getattr(keeper, field) and getattr(d, field):
                    setattr(keeper, field, getattr(d, field))

        # Move all trades and committee links from dupes to keeper
        for d in dups:
            n_trades = (
                db.query(Trade).filter(Trade.member_id == d.id).update({"member_id": keeper.id})
            )
            n_comm = (
                db.query(MemberCommittee)
                .filter(MemberCommittee.member_id == d.id)
                .update({"member_id": keeper.id})
            )
            trades_moved += n_trades
            committees_moved += n_comm
            db.delete(d)
            deleted += 1

        actions.append({
            "canonical": canonical_name,
            "chamber": chamber,
            "keeper_id": keeper.id,
            "merged_count": len(dups),
            "merged_names": [d.name for d in dups],
        })
        merged += 1

    db.commit()
    return {
        "groups_merged": merged,
        "duplicate_rows_deleted": deleted,
        "trades_reassigned": trades_moved,
        "committee_links_reassigned": committees_moved,
        "actions": actions[:30],  # truncate to avoid huge response
        "total_actions": len(actions),
    }


@router.post("/scrape/unusual-whales")
def trigger_unusual_whales_scrape(background_tasks: BackgroundTasks):
    """Pull recent trades from the Unusual Whales public politics page."""
    background_tasks.add_task(_run_uw_scrape)
    return {"message": "Unusual Whales scrape started in background."}


def _run_uw_scrape():
    import asyncio
    import logging

    from src.db.database import SessionLocal
    from src.scrapers.unusual_whales import scrape_unusual_whales
    from src.services.trade_service import ingest_trades

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        loop = asyncio.new_event_loop()
        trades = loop.run_until_complete(scrape_unusual_whales(max_pages=50))
        loop.close()
        new_count = ingest_trades(db, trades)
        logger.info(f"Unusual Whales scrape: {len(trades)} scraped, {new_count} new")
    except Exception as e:
        logger.error(f"Unusual Whales scrape failed: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/scrape/backfill-all-politicians")
def trigger_backfill_all_politicians(background_tasks: BackgroundTasks):
    """One-shot backfill: scrape every politician on Capitol Trades and ingest.

    The regular /api/scrape path silently fails to deep-scrape on Railway
    for unknown reasons. This endpoint uses the proven direct-call pattern
    that worked for the Bresnahan debug endpoint — politician list, then
    deep scrape each one, then ingest.
    """
    background_tasks.add_task(_run_backfill_all_politicians)
    return {"message": "All-politician backfill started in background."}


def _run_backfill_all_politicians():
    """Direct backfill path that proved to work via the Bresnahan debug endpoint.
    Scrapes every politician on Capitol Trades and ingests trades into the DB.
    """
    import asyncio
    import logging
    import httpx

    from src.db.database import SessionLocal
    from src.scrapers.capitol_trades import (
        _get_top_politician_ids,
        _scrape_politician_trades,
    )
    from src.services.trade_service import ingest_trades

    logger = logging.getLogger(__name__)

    async def run():
        async with httpx.AsyncClient(
            timeout=60,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            pols = await _get_top_politician_ids(client)
            logger.info(f"Backfill: got {len(pols)} politicians")
            total_new = 0
            for i, (bio_id, name) in enumerate(pols):
                try:
                    trades = await _scrape_politician_trades(client, bio_id, 96)
                    if trades:
                        db = SessionLocal()
                        try:
                            new_count = ingest_trades(db, trades)
                            total_new += new_count
                            logger.info(
                                f"[{i + 1}/{len(pols)}] {name} ({bio_id}): "
                                f"scraped {len(trades)}, ingested {new_count} new "
                                f"(running total: {total_new})"
                            )
                        finally:
                            db.close()
                    await asyncio.sleep(0.4)
                except Exception as e:
                    logger.error(f"Failed politician {bio_id}: {e}")
            logger.info(f"Backfill complete: {total_new} new trades")

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(run())
        loop.close()
    except Exception as e:
        logger.error(f"Backfill outer failure: {e}", exc_info=True)


@router.get("/scrape/debug-bresnahan")
async def debug_bresnahan(db: Session = Depends(get_db)):
    """Diagnostic: scrape Bresnahan's trades directly and report what we get.
    Helps distinguish "scrape fails on Railway" from "ingestion fails on Railway".
    """
    import httpx
    from src.scrapers.capitol_trades import _scrape_politician_trades
    from src.services.trade_service import ingest_trades

    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    ) as client:
        trades = await _scrape_politician_trades(client, "B001327", 96)
        scraped = len(trades)
        # Try ingesting them
        new = ingest_trades(db, trades) if trades else 0
        # Count Bresnahan trades in DB now
        from src.db.models import Member, Trade
        bres_count = (
            db.query(Trade)
            .join(Member)
            .filter(Member.name.ilike("%bresnahan%"))
            .count()
        )
        sample = trades[:3] if trades else []
        return {
            "scraped": scraped,
            "new_ingested": new,
            "bresnahan_in_db": bres_count,
            "sample": [
                {
                    "member_name": t.get("member_name"),
                    "ticker": t.get("ticker"),
                    "tx_date": t["transaction_date"].isoformat() if t.get("transaction_date") else None,
                    "tx_type": t.get("transaction_type"),
                    "amount_low": t.get("amount_low"),
                }
                for t in sample
            ],
        }


@router.get("/scrape/debug-politicians")
async def debug_politicians():
    """Diagnostic: hit the Capitol Trades politicians page and report how many
    bioguide IDs the scraper extracts. Used to confirm Phase 2 extraction is
    working on the live deploy.
    """
    import httpx
    from src.scrapers.capitol_trades import _get_top_politician_ids

    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    ) as client:
        pols = await _get_top_politician_ids(client)
        bres_present = any(p[0] == "B001327" for p in pols)
        return {
            "politicians_extracted": len(pols),
            "bresnahan_in_list": bres_present,
            "sample_first_5": [{"id": p[0], "name": p[1]} for p in pols[:5]],
        }


@router.post("/backfill")
def trigger_backfill(background_tasks: BackgroundTasks):
    """Full historical backfill — Capitol Trades (400 pages) + all sources.

    This can take 5-10 minutes due to rate-limiting delays.
    """
    background_tasks.add_task(_run_scrape, "backfill")
    return {"message": "Full backfill started. Capitol Trades (400 pages) — this will take 5-10 min."}
