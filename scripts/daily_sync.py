"""Off-Railway data sync — run by GitHub Actions on a schedule.

Railway's server IP is blocked by the data sources, so the fetch must happen
off-box. GitHub Actions runners have their own (non-blocked) IPs. This script:
  1. Fetches the Unusual Whales public politics page (primary source)
  2. Fetches Capitol Trades recent pages (redundant source)
  3. POSTs both to the Railway ingest endpoints (parsing happens server-side)
  4. Triggers dedupe + name normalization
  5. Fires the Slack daily report
  6. Exits non-zero if data looks stale, so the GH Actions run shows red
     and emails on failure (the watchdog that prevents silent multi-week gaps).

No secrets required — sources are public and the Railway admin endpoints are
unauthenticated. The Slack webhook lives in Railway's env, not here.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

B = "https://sub-nancy-production.up.railway.app"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def get(url, timeout=45):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=timeout).read().decode()


def post(path, obj, timeout=150):
    req = urllib.request.Request(
        B + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        print(f"  POST {path} failed: {e}")
        return {}


def main():
    new = 0

    # Source 1: Unusual Whales public page
    raw = []
    for p in range(1, 13):
        url = "https://unusualwhales.com/politics" + (f"?page={p}" if p > 1 else "")
        try:
            html = get(url)
        except Exception as e:
            print(f"UW page {p} fetch failed: {e}")
            break
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html, re.DOTALL)
        if not m:
            break
        td = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("trade_data", [])
        if not td:
            break
        raw.extend(td)
    for i in range(0, len(raw), 400):
        new += post("/api/admin/ingest-uw-raw", {"raw": raw[i:i + 400]}).get("new", 0)
    print(f"UW: fetched {len(raw)} raw trades")

    # Source 2: Capitol Trades (redundancy)
    ct_pages = 0
    for p in range(1, 6):
        try:
            html = get(f"https://www.capitoltrades.com/trades?page={p}&pageSize=96")
        except Exception as e:
            print(f"CT page {p} fetch failed: {e}")
            break
        r = post("/api/admin/ingest-ct-html", {"html": html})
        new += r.get("new", 0)
        ct_pages += 1
    print(f"Capitol Trades: fetched {ct_pages} pages")
    print(f"TOTAL NEW INGESTED: {new}")

    # Cleanup: dedupe + normalize names
    post("/api/admin/dedupe-smart", {})
    post("/api/admin/normalize-member-names", {})

    # Slack daily report
    slack = post("/api/admin/slack-daily-summary", {})
    print(f"Slack sent: {slack.get('sent')}")

    # Watchdog: fail the run (red X + email) if data is stale
    health = json.load(urllib.request.urlopen(B + "/api/health", timeout=30))
    lf = (health.get("latest_filing_date") or "")[:10]
    total = health.get("total_trades")
    print(f"latest_filing={lf} total={total}")
    if lf:
        days_behind = (datetime.now(timezone.utc).date() - datetime.strptime(lf, "%Y-%m-%d").date()).days
        if days_behind > 3:
            print(f"::error::DATA STALE — latest filing {lf} is {days_behind} days behind")
            sys.exit(1)
    print("sync OK")


if __name__ == "__main__":
    main()
