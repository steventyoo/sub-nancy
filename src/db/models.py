import datetime
import json

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import relationship

from src.db.database import Base


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    chamber = Column(String(10), nullable=False)  # House / Senate
    state = Column(String(2))
    district = Column(String(10))
    party = Column(String(20))
    active = Column(Boolean, default=True)

    trades = relationship("Trade", back_populates="member")

    __table_args__ = (
        Index("ix_members_name", "name"),
        Index("ix_members_chamber", "chamber"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    transaction_date = Column(DateTime)
    filing_date = Column(DateTime)
    ticker = Column(String(20))
    asset_description = Column(Text)
    asset_type = Column(String(50))
    transaction_type = Column(String(50))  # Purchase, Sale, Sale (Full), Sale (Partial), Exchange
    amount_low = Column(Float)
    amount_high = Column(Float)
    owner = Column(String(20))  # Self, Spouse, Child, Joint
    sector = Column(String(100))
    industry = Column(String(100))
    source_url = Column(Text)
    raw_filing_url = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    member = relationship("Member", back_populates="trades")

    __table_args__ = (
        Index("ix_trades_ticker", "ticker"),
        Index("ix_trades_transaction_date", "transaction_date"),
        Index("ix_trades_filing_date", "filing_date"),
        Index("ix_trades_transaction_type", "transaction_type"),
        Index("ix_trades_sector", "sector"),
        Index("ix_trades_created_at", "created_at"),
        Index("ix_trades_member_id", "member_id"),
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    filters_json = Column(Text, default="{}")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def filters(self) -> dict:
        return json.loads(self.filters_json) if self.filters_json else {}

    @filters.setter
    def filters(self, value: dict):
        self.filters_json = json.dumps(value)


class Sector(Base):
    __tablename__ = "sectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), unique=True, nullable=False)
    sector = Column(String(100))
    industry = Column(String(100))
    company_name = Column(String(200))

    __table_args__ = (
        Index("ix_sectors_ticker", "ticker"),
        Index("ix_sectors_sector", "sector"),
    )
