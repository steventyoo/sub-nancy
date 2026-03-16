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


def _format_repeat_buyer_html(alerts: list[dict]) -> str:
    """Build HTML section for repeat buyer alerts."""
    if not alerts:
        return ""

    rows = ""
    for a in alerts:
        rows += f"""
        <tr style="border-bottom: 1px solid #fde68a;">
          <td style="padding: 10px 8px; font-weight: 600;">{a['member_name']}</td>
          <td style="padding: 10px 8px;">{a['ticker']}</td>
          <td style="padding: 10px 8px;">{a['sector']}</td>
          <td style="padding: 10px 8px; font-weight: 700; color: #d97706;">{a['total_purchases']}x</td>
          <td style="padding: 10px 8px;">{a.get('transaction_date', 'N/A')}</td>
        </tr>"""

    return f"""
    <div style="margin-top: 20px; border: 2px solid #f59e0b; border-radius: 12px; overflow: hidden;">
      <div style="background: #fffbeb; padding: 16px 24px; border-bottom: 1px solid #fde68a;">
        <h2 style="margin: 0; font-size: 18px; color: #92400e;">Repeat Buyer Alert</h2>
        <p style="margin: 4px 0 0; font-size: 13px; color: #a16207;">Members who bought the same stock again — conviction signal</p>
      </div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <thead>
          <tr style="background: #fef3c7; text-align: left;">
            <th style="padding: 10px 8px;">Member</th>
            <th style="padding: 10px 8px;">Ticker</th>
            <th style="padding: 10px 8px;">Sector</th>
            <th style="padding: 10px 8px;">Total Buys</th>
            <th style="padding: 10px 8px;">Latest Date</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>"""


def _format_correlated_trades_html(correlated: list[dict]) -> str:
    """Build HTML section for committee-correlated trades."""
    if not correlated:
        return ""

    rows = ""
    for c in correlated[:10]:  # Top 10 most suspicious
        committees_str = ", ".join(c.get("matching_committees", [])[:2])
        tx_type = c.get("transaction_type", "Unknown")
        color = "#22c55e" if "Purchase" in tx_type else "#ef4444"
        rows += f"""
        <tr style="border-bottom: 1px solid #c7d2fe;">
          <td style="padding: 10px 8px; font-weight: 600;">{c['member_name']}</td>
          <td style="padding: 10px 8px;"><span style="color: {color}; font-weight: 600;">{tx_type}</span></td>
          <td style="padding: 10px 8px; font-weight: 700;">{c['ticker']}</td>
          <td style="padding: 10px 8px;">{c['sector']}</td>
          <td style="padding: 10px 8px; font-size: 12px; color: #4338ca;">{committees_str}</td>
        </tr>"""

    return f"""
    <div style="margin-top: 20px; border: 2px solid #6366f1; border-radius: 12px; overflow: hidden;">
      <div style="background: #eef2ff; padding: 16px 24px; border-bottom: 1px solid #c7d2fe;">
        <h2 style="margin: 0; font-size: 18px; color: #3730a3;">Committee-Correlated Trades</h2>
        <p style="margin: 4px 0 0; font-size: 13px; color: #4f46e5;">Members trading stocks in sectors their committees oversee</p>
      </div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <thead>
          <tr style="background: #e0e7ff; text-align: left;">
            <th style="padding: 10px 8px;">Member</th>
            <th style="padding: 10px 8px;">Type</th>
            <th style="padding: 10px 8px;">Ticker</th>
            <th style="padding: 10px 8px;">Sector</th>
            <th style="padding: 10px 8px;">Committee</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>"""


def build_daily_email_html(
    trades: list[Trade],
    repeat_alerts: list[dict] | None = None,
    correlated_alerts: list[dict] | None = None,
) -> str:
    """Build HTML email body for daily trade notification."""
    today = datetime.utcnow().strftime("%B %d, %Y")
    purchase_count = sum(1 for t in trades if t.transaction_type and "Purchase" in t.transaction_type)
    sale_count = sum(1 for t in trades if t.transaction_type and "Sale" in t.transaction_type)

    trade_rows = "\n".join(_format_trade_html(t) for t in trades)
    repeat_section = _format_repeat_buyer_html(repeat_alerts or [])
    correlated_section = _format_correlated_trades_html(correlated_alerts or [])

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

      {repeat_section}

      {correlated_section}

      <div style="overflow-x: auto; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px; margin-top: 20px;">
        <div style="background: #f3f4f6; padding: 12px 24px; border: 1px solid #e5e7eb; border-bottom: none; border-radius: 12px 12px 0 0;">
          <h2 style="margin: 0; font-size: 16px; color: #374151;">All New Trades</h2>
        </div>
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
    """Send daily email notifications with new trades to all active subscribers.

    Includes repeat buyer alerts and committee-correlated trade flags.
    """
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

    # Get repeat buyer alerts for today's trades
    from src.services.repeat_buyer_service import get_new_repeat_buyer_alerts

    repeat_alerts = get_new_repeat_buyer_alerts(db, hours=24)

    # Get committee-correlated trades
    from src.services.committee_service import check_committee_correlation

    correlated_alerts = []
    for trade in trades:
        correlation = check_committee_correlation(db, trade)
        if correlation:
            correlated_alerts.append({
                "member_name": trade.member.name,
                "ticker": trade.ticker,
                "sector": trade.sector,
                "transaction_type": trade.transaction_type,
                "matching_committees": correlation["matching_committees"],
            })

    # Build subject with alert counts
    alert_parts = []
    if repeat_alerts:
        alert_parts.append(f"{len(repeat_alerts)} repeat buyer(s)")
    if correlated_alerts:
        alert_parts.append(f"{len(correlated_alerts)} committee-correlated")
    alert_suffix = f" | {', '.join(alert_parts)}" if alert_parts else ""

    html = build_daily_email_html(trades, repeat_alerts, correlated_alerts)
    subject = (
        f"Congressional Trade Alert: {len(trades)} new trade(s)"
        f"{alert_suffix} - {datetime.utcnow().strftime('%m/%d/%Y')}"
    )

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
