"""Committee data ingestion, member matching, and trade-committee correlation."""

import logging

from sqlalchemy.orm import Session

from src.db.models import (
    COMMITTEE_SECTOR_MAP,
    Committee,
    Member,
    MemberCommittee,
    Trade,
)
from src.scrapers.committees import fuzzy_match_member

logger = logging.getLogger(__name__)


def ingest_committees(db: Session, raw_assignments: list[dict]) -> int:
    """Ingest scraped committee assignments into the database.

    Matches scraped member names to existing Member records using fuzzy matching.
    Creates Committee records as needed.

    Returns count of new MemberCommittee links created.
    """
    # Cache all existing member names for fuzzy matching
    all_members = db.query(Member).all()
    member_name_map = {m.name: m for m in all_members}
    db_names = list(member_name_map.keys())

    # Cache existing committees by code
    existing_committees = {c.code: c for c in db.query(Committee).all()}

    # Cache existing assignments to avoid duplicates
    existing_assignments = set()
    for mc in db.query(MemberCommittee).all():
        existing_assignments.add((mc.member_id, mc.committee_id))

    new_count = 0

    for raw in raw_assignments:
        # Get or create committee
        code = raw.get("committee_code")
        if not code:
            continue

        if code not in existing_committees:
            committee = Committee(
                name=raw["committee_name"],
                chamber=raw["chamber"],
                code=code,
            )
            db.add(committee)
            db.flush()
            existing_committees[code] = committee
        else:
            committee = existing_committees[code]

        # Match member name to existing DB record
        scraped_name = raw.get("member_name", "")
        if not scraped_name:
            continue

        # Try exact match first
        member = member_name_map.get(scraped_name)
        if not member:
            # Fuzzy match
            matched_name = fuzzy_match_member(scraped_name, db_names)
            if matched_name:
                member = member_name_map[matched_name]

        if not member:
            logger.debug(f"Could not match member: {scraped_name}")
            continue

        # Check if assignment already exists
        if (member.id, committee.id) in existing_assignments:
            continue

        mc = MemberCommittee(
            member_id=member.id,
            committee_id=committee.id,
            role=raw.get("role", "Member"),
        )
        db.add(mc)
        existing_assignments.add((member.id, committee.id))
        new_count += 1

    db.commit()
    logger.info(f"Ingested {new_count} committee assignments")
    return new_count


def get_member_committees(db: Session, member_id: int) -> list[dict]:
    """Get all committee assignments for a member."""
    assignments = (
        db.query(MemberCommittee, Committee)
        .join(Committee, MemberCommittee.committee_id == Committee.id)
        .filter(MemberCommittee.member_id == member_id)
        .all()
    )
    return [
        {
            "committee_name": c.name,
            "committee_code": c.code,
            "chamber": c.chamber,
            "role": mc.role,
        }
        for mc, c in assignments
    ]


def get_committee_members(db: Session, committee_code: str) -> list[dict]:
    """Get all members of a committee with their trade stats."""
    assignments = (
        db.query(MemberCommittee, Member, Committee)
        .join(Member, MemberCommittee.member_id == Member.id)
        .join(Committee, MemberCommittee.committee_id == Committee.id)
        .filter(Committee.code == committee_code)
        .all()
    )
    return [
        {
            "member_name": m.name,
            "chamber": m.chamber,
            "party": m.party,
            "state": m.state,
            "role": mc.role,
        }
        for mc, m, c in assignments
    ]


def get_relevant_sectors_for_member(db: Session, member_id: int) -> set[str]:
    """Get the set of stock sectors relevant to a member's committee assignments."""
    committees = get_member_committees(db, member_id)
    relevant_sectors = set()

    for c in committees:
        committee_name_lower = c["committee_name"].lower()
        for keyword, sectors in COMMITTEE_SECTOR_MAP.items():
            if keyword in committee_name_lower:
                relevant_sectors.update(sectors)

    return relevant_sectors


def check_committee_correlation(db: Session, trade: Trade) -> dict | None:
    """Check if a trade is correlated with the member's committee assignments.

    Returns correlation info if the trade's sector matches a committee's jurisdiction.
    Returns None if no correlation found.
    """
    if not trade.sector or not trade.member_id:
        return None

    relevant_sectors = get_relevant_sectors_for_member(db, trade.member_id)

    if trade.sector in relevant_sectors:
        # Find which committees are relevant
        committees = get_member_committees(db, trade.member_id)
        matching_committees = []
        for c in committees:
            committee_name_lower = c["committee_name"].lower()
            for keyword, sectors in COMMITTEE_SECTOR_MAP.items():
                if keyword in committee_name_lower and trade.sector in sectors:
                    matching_committees.append(c["committee_name"])
                    break

        return {
            "is_correlated": True,
            "trade_sector": trade.sector,
            "matching_committees": matching_committees,
            "signal": "COMMITTEE_CORRELATED",
        }

    return None


def get_committee_trades(
    db: Session,
    committee_code: str,
    days: int = 90,
) -> list[dict]:
    """Get all trades by members of a committee, flagging sector-correlated ones."""
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get committee info and relevant sectors
    committee = db.query(Committee).filter(Committee.code == committee_code).first()
    if not committee:
        return []

    # Get relevant sectors for this committee
    relevant_sectors = set()
    committee_name_lower = committee.name.lower()
    for keyword, sectors in COMMITTEE_SECTOR_MAP.items():
        if keyword in committee_name_lower:
            relevant_sectors.update(sectors)

    # Get member IDs on this committee
    member_ids = [
        mc.member_id
        for mc in db.query(MemberCommittee)
        .filter(MemberCommittee.committee_id == committee.id)
        .all()
    ]

    if not member_ids:
        return []

    # Get trades by those members
    trades = (
        db.query(Trade)
        .join(Member)
        .filter(Trade.member_id.in_(member_ids))
        .filter(Trade.transaction_date >= cutoff)
        .order_by(Trade.transaction_date.desc())
        .limit(200)
        .all()
    )

    results = []
    for t in trades:
        is_correlated = t.sector in relevant_sectors if t.sector else False
        results.append({
            "member_name": t.member.name,
            "party": t.member.party,
            "ticker": t.ticker,
            "sector": t.sector,
            "transaction_type": t.transaction_type,
            "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
            "amount_low": t.amount_low,
            "amount_high": t.amount_high,
            "committee_correlated": is_correlated,
            "committee_name": committee.name,
        })

    # Sort correlated trades first
    results.sort(key=lambda x: (not x["committee_correlated"], x["transaction_date"] or ""), reverse=False)
    results.sort(key=lambda x: x["committee_correlated"], reverse=True)

    return results
