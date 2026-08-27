"""Local backfill: scrape Capitol Trades from this (non-throttled) IP and
POST results to production via /api/admin/ingest-trades.

Capitol Trades throttles Railway's IP under load, but not a laptop. This
runs the deep scrape locally and pushes batches to prod, closing the
historical gap the UW API tier can't serve.
"""
import asyncio
import json
import re
import sys
import time
import urllib.request

import httpx

sys.path.insert(0, ".")
from src.scrapers.capitol_trades import _scrape_politician_trades

B = "https://sub-nancy-production.up.railway.app"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def get_gaps(min_gap=20):
    url = f"{B}/api/admin/cross-source-audit?min_gap={min_gap}"
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r).get("gaps", [])


def total():
    with urllib.request.urlopen(f"{B}/api/health", timeout=30) as r:
        return json.load(r).get("total_trades", 0)


def post_trades(trades):
    out = []
    for t in trades:
        d = dict(t)
        for k in ("transaction_date", "filing_date"):
            v = d.get(k)
            if v is not None and not isinstance(v, str):
                d[k] = v.isoformat()
        out.append(d)
    body = json.dumps({"trades": out}).encode()
    req = urllib.request.Request(
        f"{B}/api/admin/ingest-trades", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


async def wait_for_endpoint():
    for _ in range(40):
        try:
            req = urllib.request.Request(
                f"{B}/api/admin/ingest-trades", data=b'{"trades":[]}',
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(15)
    return False


async def main():
    if not await wait_for_endpoint():
        print("ingest endpoint never came up", flush=True)
        return
    start = total()
    print(f"START total={start}", flush=True)
    gaps = get_gaps()
    print(f"gap politicians: {len(gaps)}", flush=True)
    cum = 0
    async with httpx.AsyncClient(headers=HDR, follow_redirects=True, timeout=45) as client:
        for g in gaps:
            last = g["uw_name"].split()[-1]
            our_n = g.get("our_count", 0) or 0
            try:
                sr = await client.get(f"https://www.capitoltrades.com/politicians?search={last}")
                ids = list(dict.fromkeys(re.findall(r"/politicians/([A-Z]\d+)", sr.text)))
            except Exception:
                ids = []
            if not ids:
                continue
            # Skip the pages we almost certainly already have: start a few pages
            # below where our current count ends (96 trades/page). This avoids
            # re-scraping/re-POSTing ~100 pages of dupes per whale.
            start_page = max(1, our_n // 96 - 3)
            try:
                trades = await _scrape_politician_trades(
                    client, ids[0], 96, max_pages=200, start_page=start_page)
            except Exception as e:
                print(f"  scrape fail {g['uw_name']}: {e}", flush=True)
                continue
            if not trades:
                continue
            new = 0
            for i in range(0, len(trades), 400):
                try:
                    new += post_trades(trades[i:i + 400]).get("new", 0)
                except Exception:
                    pass
            cum += new
            if new > 0:
                print(f"  {g['uw_name']}: from p{start_page}, scraped {len(trades)}, +{new} (cum {cum}, total~{start+cum})", flush=True)
            await asyncio.sleep(1)
    # cleanup
    for ep in ("dedupe-smart", "normalize-member-names"):
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{B}/api/admin/{ep}", data=b"", method="POST"), timeout=120)
        except Exception:
            pass
    print(f"DONE +{cum} new | total {start} -> {total()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
