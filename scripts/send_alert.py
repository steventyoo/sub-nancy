"""Daily trade alert — runs on GitHub Actions (which can reach Gmail SMTP;
Railway blocks outbound SMTP ports, so the send has to happen off-Railway).

Fetches the newest congressional filings from the Railway API, builds an HTML
digest, and emails it via Gmail SMTP to ALERT_RECIPIENTS.

Env (GitHub Actions secrets/vars):
  GMAIL_APP_PASSWORD  – 16-char Google app password (required)
  GMAIL_USER          – sender gmail (default steventyoo@gmail.com)
  ALERT_RECIPIENTS    – comma-separated recipients
"""
import json
import os
import smtplib
import sys
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

B = "https://sub-nancy-production.up.railway.app"
SINCE_DAYS = 3


def main():
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    user = os.environ.get("GMAIL_USER", "steventyoo@gmail.com").strip()
    recipients = [e.strip() for e in os.environ.get(
        "ALERT_RECIPIENTS", "steventyoo@gmail.com").split(",") if e.strip()]
    if not pw:
        print("GMAIL_APP_PASSWORD not set"); sys.exit(1)

    # Pull newest filings (already sorted by filing date desc)
    with urllib.request.urlopen(f"{B}/api/trades/recent?limit=60", timeout=45) as r:
        trades = json.load(r)
    if not trades:
        print("no trades"); return

    def amt(t):
        lo, hi = t.get("amount_low"), t.get("amount_high")
        if lo and hi:
            return f"${int(lo):,}–${int(hi):,}"
        if lo:
            return f"${int(lo):,}+"
        return "—"

    rows = ""
    for t in trades:
        typ = t.get("transaction_type") or ""
        color = "#16a34a" if "Purchase" in typ else "#dc2626"
        rows += (
            "<tr style='border-bottom:1px solid #eee'>"
            f"<td style='padding:5px 10px;font-weight:600'>{t.get('member_name','?')}</td>"
            f"<td style='padding:5px 10px'>{t.get('ticker') or (t.get('asset_description') or '')[:22]}</td>"
            f"<td style='padding:5px 10px;color:{color};font-weight:600'>{typ}</td>"
            f"<td style='padding:5px 10px'>{amt(t)}</td>"
            f"<td style='padding:5px 10px'>{(t.get('transaction_date') or '')[:10]}</td>"
            f"<td style='padding:5px 10px;color:#666'>{(t.get('filing_date') or '')[:10]}</td>"
            "</tr>"
        )

    newest = max(((t.get("filing_date") or "")[:10] for t in trades), default="?")
    html = (
        "<div style='font-family:system-ui,sans-serif;max-width:760px'>"
        "<h2 style='margin:0 0 4px'>Subversive — New Congressional Filings</h2>"
        f"<p style='color:#666;margin:0 0 14px'>Newest filing {newest} · {len(trades)} recent disclosures</p>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
        "<thead><tr style='text-align:left;border-bottom:2px solid #111'>"
        "<th style='padding:5px 10px'>Member</th><th style='padding:5px 10px'>Ticker</th>"
        "<th style='padding:5px 10px'>Type</th><th style='padding:5px 10px'>Amount</th>"
        "<th style='padding:5px 10px'>Traded</th><th style='padding:5px 10px'>Filed</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p style='color:#999;font-size:12px;margin-top:16px'>"
        "<a href='https://sub-nancy-production.up.railway.app/'>Open the Subversive dashboard</a></p></div>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Subversive: new congressional filings (through {newest})"
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, recipients, msg.as_string())
    print(f"ALERT SENT to {recipients} | newest filing {newest} | {len(trades)} trades")


if __name__ == "__main__":
    main()
