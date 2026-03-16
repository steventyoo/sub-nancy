"""Repeat buyer detection — flags when members of Congress buy the same stock multiple times."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from src.db.models import Member, Trade

logger = logging.getLogger(__name__)


def detect_repeat_buyers(
    db: Session,
    lookback_days: int = 180,
    min_purchases: int = 2,
) -> list[dict]:
    """Find all member-ticker pairs where a member has bought the same stock
    multiple times in the lookback period.

    Returns list of repeat buyer signals sorted by conviction (purchase count).
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    rows = (
        db.query(
            Member.name,
            Member.chamber,
            Member.party,
            Trade.ticker,
            Trade.sector,
            func.count(Trade.id).label("purchase_count"),
            func.sum(Trade.amount_low).label("total_volume"),
            func.min(Trade.transaction_date).label("first_purchase"),
            func.max(Trade.transaction_date).label("last_purchase"),
        )
        .join(Trade)
        .filter(
            Trade.transaction_type.ilike("%purchase%"),
            Trade.ticker.isnot(None),
            Trade.ticker != "",
            Trade.transaction_date >= cutoff,
        )
        .group_by(Member.name, Member.chamber, Member.party, Trade.ticker, Trade.sector)
        .having(func.count(Trade.id) >= min_purchases)
        .order_by(func.count(Trade.id).desc())
        .all()
    )

    return [
        {
            "member_name": r.name,
            "chamber": r.chamber,
            "party": r.party or "N/A",
            "ticker": r.ticker,
            "sector": r.sector or "N/A",
            "purchase_count": r.purchase_count,
            "total_volume": round(r.total_volume) if r.total_volume else 0,
            "first_purchase": r.first_purchase.isoformat() if r.first_purchase else None,
            "last_purchase": r.last_purchase.isoformat() if r.last_purchase else None,
            "signal": "REPEAT_BUYER",
        }
        for r in rows
    ]


def check_repeat_buyer(
    db: Session,
    member_id: int,
    ticker: str,
    lookback_days: int = 180,
) -> dict | None:
    """Check if a specific member has previously purchased a specific ticker.

    Used during trade ingestion to flag new trades as repeat buys.
    """
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    prior_purchases = (
        db.query(Trade)
        .filter(
            Trade.member_id == member_id,
            Trade.ticker == ticker,
            Trade.transaction_type.ilike("%purchase%"),
            Trade.transaction_date >= cutoff,
        )
        .order_by(Trade.transaction_date.desc())
        .all()
    )

    if not prior_purchases:
        return None

    return {
        "is_repeat": True,
        "prior_purchase_count": len(prior_purchases),
        "prior_dates": [
            t.transaction_date.isoformat()
            for t in prior_purchases
            if t.transaction_date
        ],
        "total_prior_volume": sum(t.amount_low or 0 for t in prior_purchases),
    }


def get_new_repeat_buyer_alerts(db: Session, hours: int = 24) -> list[dict]:
    """Get repeat buyer alerts for trades ingested in the last N hours.

    Used by the email service to highlight repeat buys in daily notifications.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Get recent purchase trades
    recent_purchases = (
        db.query(Trade)
        .join(Member)
        .filter(
            Trade.created_at >= cutoff,
            Trade.transaction_type.ilike("%purchase%"),
            Trade.ticker.isnot(None),
            Trade.ticker != "",
        )
        .all()
    )

    alerts = []
    for trade in recent_purchases:
        # Check for prior purchases of this ticker by this member (before this trade)
        prior_count = (
            db.query(func.count(Trade.id))
            .filter(
                Trade.member_id == trade.member_id,
                Trade.ticker == trade.ticker,
                Trade.transaction_type.ilike("%purchase%"),
                Trade.id != trade.id,  # Exclude the current trade
                Trade.transaction_date >= datetime.utcnow() - timedelta(days=180),
            )
            .scalar()
        )

        if prior_count and prior_count >= 1:
            alerts.append({
                "member_name": trade.member.name,
                "chamber": trade.member.chamber,
                "party": trade.member.party or "N/A",
                "ticker": trade.ticker,
                "sector": trade.sector or "N/A",
                "transaction_date": trade.transaction_date.isoformat() if trade.transaction_date else None,
                "amount_low": trade.amount_low,
                "amount_high": trade.amount_high,
                "prior_purchases": prior_count,
                "total_purchases": prior_count + 1,  # Including this one
                "signal": "REPEAT_BUYER",
            })

    return alerts
