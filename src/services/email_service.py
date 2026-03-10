"""Email notification service using Resend."""

import logging
from datetime import datetime

import resend
from sqlalchemy.orm import Session

from src.config import settings
from src.db.models import Member, Subscriber, Trade
from src.services.trade_service import get_recent_trades

logger = logging.getLogger(__name__)


def _format_amount(low: float | None, high: float | None) -> str:
    if low is None:
        return "N/A"
    if high is None:
        return f"Over ${low:,.0f}"
    return f"${low:,.0f} - ${high:,.0f}"


def _format_trade_html(trade: Trade) -> str:
    member = trade.member
    tx_type = trade.transaction_type or "Unknown"
    color = "#22c55e" if "Purchase" in tx_type else "#ef4444" if "Sale" in tx_type else "#6b7280"

    return f"""
    <tr style="border-bottom: 1px solid #e5e7eb;">
      <td style="padding: 12px 8px; font-weight: 600;">{member.name}</td>
      <td style="padding: 12px 8px;">{member.chamber}</td>
      <td style="padding: 12px 8px;">
        <span style="color: {color}; font-weight: 600;">{tx_type}</span>
      </td>
      <td style="padding: 12px 8px; font-weight: 600;">{trade.ticker or 'N/A'}</td>
      <td style="padding: 12px 8px;">{trade.asset_description or ''}</td>
      <td style="padding: 12px 8px;">{_format_amount(trade.amount_low, trade.amount_high)}</td>
      <td style="padding: 12px 8px;">{trade.transaction_date.strftime('%m/%d/%Y') if trade.transaction_date else 'N/A'}</td>
      <td style="padding: 12px 8px;">{trade.sector or 'N/A'}</td>
    </tr>"""


def build_daily_email_html(trades: list[Trade]) -> str:
    """Build HTML email body for daily trade notification."""
    today = datetime.utcnow().strftime("%B %d, %Y")
    purchase_count = sum(1 for t in trades if t.transaction_type and "Purchase" in t.transaction_type)
    sale_count = sum(1 for t in trades if t.transaction_type and "Sale" in t.transaction_type)

    trade_rows = "\n".join(_format_trade_html(t) for t in trades)

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; color: #1f2937;">
      <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%); color: white; padding: 24px; border-radius: 12px 12px 0 0;">
        <h1 style="margin: 0; font-size: 24px;">Congressional Trade Alert</h1>
        <p style="margin: 8px 0 0; opacity: 0.9;">{today} &mdash; {len(trades)} new trade(s) detected</p>
      </div>

      <div style="background: #f9fafb; padding: 16px 24px; border-left: 1px solid #e5e7eb; border-right: 1px solid #e5e7eb;">
        <span style="background: #22c55e; color: white; padding: 4px 12px; border-radius: 9999px; font-size: 14px; margin-right: 8px;">
          {purchase_count} Purchases
        </span>
        <span style="background: #ef4444; color: white; padding: 4px 12px; border-radius: 9999px; font-size: 14px;">
          {sale_count} Sales
        </span>
      </div>

      <div style="overflow-x: auto; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
          <thead>
            <tr style="background: #f3f4f6; text-align: left;">
              <th style="padding: 12px 8px;">Member</th>
              <th style="padding: 12px 8px;">Chamber</th>
              <th style="padding: 12px 8px;">Type</th>
              <th style="padding: 12px 8px;">Ticker</th>
              <th style="padding: 12px 8px;">Asset</th>
              <th style="padding: 12px 8px;">Amount</th>
              <th style="padding: 12px 8px;">Date</th>
              <th style="padding: 12px 8px;">Sector</th>
            </tr>
          </thead>
          <tbody>
            {trade_rows}
          </tbody>
        </table>
      </div>

      <div style="margin-top: 24px; padding: 16px; background: #f3f4f6; border-radius: 8px; font-size: 12px; color: #6b7280;">
        <p>Data sourced from official House and Senate financial disclosures.</p>
        <p>Transaction amounts are reported as ranges per the STOCK Act.</p>
      </div>
    </body>
    </html>
    """


def send_daily_notifications(db: Session):
    """Send daily email notifications with new trades to all active subscribers."""
    resend.api_key = settings.resend_api_key

    trades = get_recent_trades(db, hours=24)
    if not trades:
        logger.info("No new trades in the last 24 hours, skipping email")
        return

    # Eagerly load member relationships
    for trade in trades:
        _ = trade.member

    subscribers = db.query(Subscriber).filter(Subscriber.active.is_(True)).all()
    if not subscribers:
        logger.info("No active subscribers, skipping email")
        return

    html = build_daily_email_html(trades)
    subject = f"Congressional Trade Alert: {len(trades)} new trade(s) - {datetime.utcnow().strftime('%m/%d/%Y')}"

    for sub in subscribers:
        try:
            resend.Emails.send({
                "from": settings.email_from,
                "to": [sub.email],
                "subject": subject,
                "html": html,
            })
            logger.info(f"Sent daily email to {sub.email}")
        except Exception as e:
            logger.error(f"Failed to send email to {sub.email}: {e}")
