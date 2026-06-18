"""Main FastAPI application."""

import base64
import logging
import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from src.api.routes import router
from src.config import settings
from src.db.database import init_db, SessionLocal
from src.db.seed import seed_sectors

IS_VERCEL = os.environ.get("VERCEL", "") == "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Subversive ETF",
    description="Congressional stock trade tracker with NL queries and email alerts",
    version="0.1.0",
)

app.include_router(router)


# --- Basic Auth Middleware (if ADMIN_USER and ADMIN_PASS are set) ---
@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if not settings.admin_user or not settings.admin_pass:
        return await call_next(request)  # No auth configured, allow all

    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            if secrets.compare_digest(user, settings.admin_user) and secrets.compare_digest(passwd, settings.admin_pass):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Subversive ETF"'},
        content="Unauthorized",
    )


@app.on_event("startup")
def startup():
    # Initialize database and seed data (skip on Vercel — tables already exist)
    if not IS_VERCEL:
        init_db()
        db = SessionLocal()
        try:
            seed_sectors(db)
        finally:
            db.close()

    # Only start scheduler for local/persistent server (not Vercel)
    if not IS_VERCEL:
        from apscheduler.schedulers.background import BackgroundScheduler
        from src.scheduler.jobs import run_scrape_job

        global _scheduler
        _scheduler = BackgroundScheduler()
        # Fixed daily cron (not interval) so deploys/restarts don't reset the timer
        _scheduler.add_job(
            run_scrape_job,
            "cron",
            hour=6,
            minute=0,
            id="scrape_job_am",
            name="Scrape congressional disclosures (AM)",
        )
        _scheduler.add_job(
            run_scrape_job,
            "cron",
            hour=18,
            minute=0,
            id="scrape_job_pm",
            name="Scrape congressional disclosures (PM)",
        )
        # Email alerts removed — daily Slack report replaces them.
        _scheduler.start()
        logger.info("Scheduler started: scraping at 06:00 & 18:00 UTC")

        # Run a scrape immediately on startup so data is never stale after a deploy
        import threading
        threading.Thread(target=run_scrape_job, daemon=True).start()
        logger.info("Startup scrape kicked off in background thread")


_scheduler = None


@app.on_event("shutdown")
def shutdown():
    if _scheduler:
        _scheduler.shutdown()


@app.get("/", response_class=HTMLResponse)
def root():
    return DASHBOARD_HTML


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Subversive ETF — Congressional Trade Tracker</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --cream: #E0DCCC;
    --cream-light: #F9F8F4;
    --cream-dark: #c9c4b1;
    --black: #1a1a1a;
    --gray: #6b6b6b;
    --gray-light: #a0a0a0;
    --white: #ffffff;
    --green: #2d6a4f;
    --red: #9d0208;
    --border: #e0e0e0;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'IBM Plex Sans', -apple-system, sans-serif;
    background: var(--white);
    color: var(--black);
    min-height: 100vh;
  }

  /* HEADER */
  .header {
    background: var(--black);
    padding: 20px 40px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    color: var(--white);
    letter-spacing: -0.5px;
  }
  .header h1 span { color: var(--cream); }
  .header-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: var(--gray-light);
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }

  /* NAV TABS */
  .nav-bar {
    border-bottom: 3px solid var(--black);
    padding: 0 40px;
    display: flex;
    gap: 0;
    background: var(--white);
  }
  .nav-tab {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 14px 28px;
    cursor: pointer;
    color: var(--gray);
    border-bottom: 3px solid transparent;
    margin-bottom: -3px;
    transition: all 0.15s;
  }
  .nav-tab:hover { color: var(--black); }
  .nav-tab.active {
    color: var(--black);
    border-bottom-color: var(--cream);
    background: var(--cream-light);
  }

  /* CONTAINER */
  .container { max-width: 1100px; margin: 0 auto; padding: 40px; }

  /* QUERY SECTION */
  .section-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--gray);
    margin-bottom: 16px;
  }
  .query-card {
    background: var(--cream-light);
    border: 2px solid var(--cream);
    padding: 28px;
    margin-bottom: 32px;
  }
  .query-row { display: flex; gap: 0; }
  .query-input {
    flex: 1;
    padding: 14px 18px;
    border: 2px solid var(--black);
    border-right: none;
    background: var(--white);
    color: var(--black);
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 15px;
    outline: none;
  }
  .query-input:focus { border-color: var(--black); background: var(--cream-light); }
  .query-input::placeholder { color: var(--gray-light); }
  .btn {
    padding: 14px 28px;
    border: 2px solid var(--black);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn-primary {
    background: var(--black);
    color: var(--white);
  }
  .btn-primary:hover { background: #333; }
  .btn-primary:disabled { background: var(--gray-light); cursor: not-allowed; border-color: var(--gray-light); }

  /* EXAMPLE CHIPS */
  .examples { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }
  .example-chip {
    padding: 6px 16px;
    background: var(--white);
    border: 1px solid var(--cream-dark);
    color: var(--gray);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.3px;
  }
  .example-chip:hover {
    background: var(--cream);
    color: var(--black);
    border-color: var(--black);
  }

  /* RESULTS */
  .results-section { min-height: 200px; }
  .answer {
    background: var(--cream-light);
    border-left: 4px solid var(--cream);
    padding: 24px;
    margin-bottom: 20px;
    line-height: 1.8;
    font-size: 15px;
  }
  .answer strong { color: var(--black); }
  .answer ul { padding-left: 20px; }
  .answer li { margin: 4px 0; }
  .answer h3 { font-family: 'IBM Plex Mono', monospace; color: var(--black); margin: 12px 0 6px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
  .sql-block {
    background: var(--black);
    padding: 16px 20px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: var(--cream);
    margin-bottom: 20px;
    overflow-x: auto;
    letter-spacing: 0.3px;
  }

  /* TABLES */
  .trades-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .trades-table th {
    text-align: left;
    padding: 12px 14px;
    background: var(--black);
    color: var(--cream);
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .trades-table td {
    padding: 11px 14px;
    border-bottom: 1px solid var(--border);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
  }
  .trades-table tr:hover td { background: var(--cream-light); }
  .badge {
    padding: 3px 10px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-buy { background: #e8f5e9; color: var(--green); border: 1px solid #c8e6c9; }
  .badge-sell { background: #fce4ec; color: var(--red); border: 1px solid #f8bbd0; }
  .badge-other { background: var(--cream-light); color: var(--gray); border: 1px solid var(--border); }

  /* LOADING */
  .loading {
    text-align: center;
    padding: 48px;
    color: var(--gray);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .loading .spinner {
    display: inline-block;
    width: 20px;
    height: 20px;
    border: 2px solid var(--cream);
    border-top-color: var(--black);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-right: 10px;
    vertical-align: middle;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* FILTERS */
  .filters { display: flex; gap: 0; margin-bottom: 20px; flex-wrap: wrap; }
  .filter-input {
    padding: 10px 14px;
    border: 2px solid var(--black);
    border-right: none;
    background: var(--white);
    color: var(--black);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    outline: none;
  }
  .filter-input:last-of-type { border-right: none; }
  .filter-input:focus { background: var(--cream-light); }
  .filter-input::placeholder { color: var(--gray-light); }
  .filter-select {
    padding: 10px 14px;
    border: 2px solid var(--black);
    border-right: none;
    background: var(--white);
    color: var(--black);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    outline: none;
    cursor: pointer;
    -webkit-appearance: none;
    -moz-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%231a1a1a'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    padding-right: 30px;
  }

  .empty {
    text-align: center;
    padding: 48px;
    color: var(--gray-light);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  /* SUBSCRIBE */
  .subscribe-card {
    background: var(--cream-light);
    border: 2px solid var(--cream);
    padding: 36px;
    max-width: 520px;
  }
  .subscribe-card h2 {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 16px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
  }
  .subscribe-card p {
    color: var(--gray);
    font-size: 14px;
    margin-bottom: 20px;
    line-height: 1.6;
  }

  /* FOOTER */
  .footer {
    border-top: 3px solid var(--black);
    padding: 20px 40px;
    margin-top: 60px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .footer-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--gray-light);
    text-transform: uppercase;
    letter-spacing: 1px;
  }

  .count-badge {
    display: inline-block;
    background: var(--cream);
    padding: 2px 10px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    margin-left: 8px;
  }

  /* STAT CARDS */
  .stat-card {
    background: var(--cream-light);
    border: 2px solid var(--cream);
    padding: 20px 24px;
    text-align: center;
  }
  .stat-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 28px;
    font-weight: 700;
    color: var(--black);
    margin-bottom: 4px;
  }
  .stat-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--gray);
  }

  /* PARTY BADGES */
  .party-r { color: #c0392b; font-size: 10px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; margin-left: 4px; }
  .party-d { color: #2980b9; font-size: 10px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; margin-left: 4px; }
  .party-i { color: var(--gray); font-size: 10px; font-weight: 700; font-family: 'IBM Plex Mono', monospace; margin-left: 4px; }

  /* SIGNAL BADGES */
  .signal-strong-buy { background: #1b5e20; color: #fff; border: 1px solid #1b5e20; padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
  .signal-buy { background: #e8f5e9; color: var(--green); border: 1px solid #c8e6c9; padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
  .signal-sell { background: #fce4ec; color: var(--red); border: 1px solid #f8bbd0; padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
  .signal-strong-sell { background: #b71c1c; color: #fff; border: 1px solid #b71c1c; padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
  .signal-mixed { background: var(--cream-light); color: var(--gray); border: 1px solid var(--border); padding: 3px 10px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }

  /* BAR CHART */
  .bar-row { display: flex; align-items: center; margin-bottom: 6px; gap: 8px; }
  .bar-label { font-family: 'IBM Plex Mono', monospace; font-size: 12px; width: 80px; text-align: right; flex-shrink: 0; }
  .bar-track { flex: 1; height: 24px; background: var(--cream-light); border: 1px solid var(--border); position: relative; }
  .bar-fill { height: 100%; transition: width 0.5s; }
  .bar-count { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--gray); width: 40px; flex-shrink: 0; }

  /* PROFILE */
  .profile-header { display: flex; align-items: center; gap: 24px; margin-bottom: 32px; padding-bottom: 20px; border-bottom: 2px solid var(--cream); }
  .profile-name { font-family: 'IBM Plex Mono', monospace; font-size: 24px; font-weight: 700; }
  .profile-meta { font-family: 'IBM Plex Mono', monospace; font-size: 13px; color: var(--gray); }
  .profile-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 32px; }

  /* CLICKABLE MEMBER */
  .member-link { cursor: pointer; text-decoration: underline; text-decoration-color: var(--cream-dark); text-underline-offset: 2px; }
  .member-link:hover { text-decoration-color: var(--black); }

  /* DOWNLOAD BUTTON */
  .btn-download {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 18px;
    background: var(--white);
    border: 2px solid var(--black);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    cursor: pointer;
    transition: all 0.15s;
    color: var(--black);
  }
  .btn-download:hover { background: var(--black); color: var(--white); }
  .btn-download svg { width: 14px; height: 14px; }

  /* RESPONSIVE */
  @media (max-width: 768px) {
    .stat-card { padding: 14px; }
    .stat-value { font-size: 20px; }
    #dash-stats { grid-template-columns: repeat(2, 1fr) !important; gap: 8px !important; }
    .profile-stats { grid-template-columns: repeat(2, 1fr); }
    .nav-tab { padding: 10px 14px; font-size: 11px; letter-spacing: 0.5px; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>SUBVERSIVE <span>ETF</span></h1>
  <div class="header-meta">Congressional Trade Tracker</div>
</div>

<div class="nav-bar">
  <div class="nav-tab active" onclick="showTab('dashboard')">Dashboard</div>
  <div class="nav-tab" onclick="showTab('recent')">Recent</div>
  <div class="nav-tab" onclick="showTab('leaderboard')">Leaderboard</div>
  <div class="nav-tab" onclick="showTab('screener')">Screener</div>
  <div class="nav-tab" onclick="showTab('query')">Query</div>
  <div class="nav-tab" onclick="showTab('browse')">Browse</div>
  <div class="nav-tab" onclick="showTab('tidal')">Audit</div>
</div>

<div class="container">

  <!-- DASHBOARD TAB -->
  <div id="tab-dashboard">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Market Overview <span id="dash-badge" class="count-badge" style="display:none"></span></div>
      <button class="btn-download" onclick="downloadDashboardCSV()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> CSV</button>
    </div>
    <div id="dash-stats" class="stats-grid" style="display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:32px">
      <div class="stat-card"><div class="stat-value" id="ds-total">-</div><div class="stat-label">Total Trades</div></div>
      <div class="stat-card"><div class="stat-value" id="ds-filing">-</div><div class="stat-label">Disclosed / 535</div></div>
      <div class="stat-card"><div class="stat-value" id="ds-members">-</div><div class="stat-label">Members Tracked</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--green)" id="ds-buys">-</div><div class="stat-label">Purchases</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--red)" id="ds-sells">-</div><div class="stat-label">Sales</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px">
      <div>
        <div class="section-title">Top Tickers (30 Days)</div>
        <div id="dash-tickers"><div class="loading"><span class="spinner"></span> Loading...</div></div>
      </div>
      <div>
        <div class="section-title">Most Active Members (30 Days)</div>
        <div id="dash-members"><div class="loading"><span class="spinner"></span> Loading...</div></div>
      </div>
    </div>
    <div style="margin-top:32px">
      <div class="section-title">Weekly Activity</div>
      <div id="dash-activity" style="font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--gray)"></div>
    </div>
  </div>

  <!-- MEMBER PROFILE VIEW (hidden by default) -->
  <div id="tab-profile" style="display:none">
    <div style="margin-bottom:16px"><a href="#" onclick="backFromProfile();return false" style="font-family:'IBM Plex Mono',monospace;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--gray)">&larr; Back</a></div>
    <div id="profile-content"><div class="loading"><span class="spinner"></span> Loading profile...</div></div>
  </div>

  <!-- LEADERBOARD TAB -->
  <div id="tab-leaderboard" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Politician Leaderboard</div>
      <button class="btn-download" onclick="downloadLeaderboardCSV()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> CSV</button>
    </div>
    <div class="filters" style="margin-bottom:20px">
      <select class="filter-select" id="lb-period" onchange="loadLeaderboard()">
        <option value="all">All Time</option>
        <option value="30d">Last 30 Days</option>
        <option value="90d">Last 90 Days</option>
        <option value="1y">Last Year</option>
      </select>
      <select class="filter-select" id="lb-chamber" onchange="loadLeaderboard()">
        <option value="">All Chambers</option>
        <option value="House">House</option>
        <option value="Senate">Senate</option>
      </select>
    </div>
    <div id="leaderboard-results"><div class="loading"><span class="spinner"></span> Loading leaderboard...</div></div>
  </div>

  <!-- SCREENER TAB -->
  <div id="tab-screener" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Congressional Stock Screener</div>
      <button class="btn-download" onclick="downloadScreenerCSV()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> CSV</button>
    </div>
    <div class="filters" style="margin-bottom:20px">
      <select class="filter-select" id="sc-period" onchange="loadScreener()">
        <option value="7d">Last 7 Days</option>
        <option value="30d" selected>Last 30 Days</option>
        <option value="90d">Last 90 Days</option>
        <option value="1y">Last Year</option>
      </select>
    </div>
    <div id="screener-results"><div class="loading"><span class="spinner"></span> Loading screener...</div></div>
  </div>

  <!-- RECENT TRADES TAB -->
  <div id="tab-recent" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Latest Congressional Trades <span id="stats-badge" class="count-badge" style="display:none"></span></div>
      <button class="btn-download" onclick="downloadRecentCSV()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> CSV</button>
    </div>
    <div class="filters" style="margin-bottom:20px">
      <select class="filter-select" id="r-member" onchange="filterRecent()" style="min-width:200px">
        <option value="">All Members</option>
      </select>
      <select class="filter-select" id="r-ticker" onchange="filterRecent()" style="min-width:140px">
        <option value="">All Tickers</option>
      </select>
      <select class="filter-select" id="r-type" onchange="filterRecent()">
        <option value="">All Types</option>
        <option value="Purchase">Purchase</option>
        <option value="Sale">Sale</option>
      </select>
      <select class="filter-select" id="r-party" onchange="filterRecent()">
        <option value="">All Parties</option>
        <option value="Democrat">Democrat</option>
        <option value="Republican">Republican</option>
        <option value="Independent">Independent</option>
      </select>
      <select class="filter-select" id="r-chamber" onchange="filterRecent()">
        <option value="">All Chambers</option>
        <option value="House">House</option>
        <option value="Senate">Senate</option>
      </select>
      <select class="filter-select" id="r-min-amount" onchange="filterRecent()">
        <option value="">Any Size</option>
        <option value="15001">$15K+</option>
        <option value="50001">$50K+</option>
        <option value="100001">$100K+</option>
        <option value="250001">$250K+</option>
        <option value="500001">$500K+</option>
        <option value="1000001">$1M+</option>
        <option value="5000001">$5M+</option>
      </select>
      <button class="btn btn-primary" onclick="clearFilters()" style="font-size:11px;padding:10px 18px">Clear</button>
    </div>
    <div class="results-section" id="recent-results">
      <div class="loading"><span class="spinner"></span> Loading recent trades...</div>
    </div>
  </div>

  <!-- QUERY TAB -->
  <div id="tab-query" style="display:none">
    <div class="section-title">Natural Language Query</div>
    <div class="query-card">
      <div class="query-row">
        <input class="query-input" id="question" placeholder="Which senators bought defense stocks before the Iran conflict?" onkeydown="if(event.key==='Enter')askQuestion()">
        <button class="btn btn-primary" id="ask-btn" onclick="askQuestion()">Ask</button>
      </div>
      <div class="examples">
        <div class="example-chip" onclick="setQ('which senators bought defense stocks in 2020?')">Defense 2020</div>
        <div class="example-chip" onclick="setQ('who sold healthcare stocks?')">Healthcare Sales</div>
        <div class="example-chip" onclick="setQ('show me trades over 500k')">Trades &gt;500K</div>
        <div class="example-chip" onclick="setQ('show me all of Kelly Loeffler trades')">Kelly Loeffler</div>
        <div class="example-chip" onclick="setQ('which senators traded tech stocks?')">Tech Trades</div>
        <div class="example-chip" onclick="setQ('who bought energy stocks in 2019?')">Energy 2019</div>
      </div>
    </div>
    <div class="results-section" id="results">
      <div class="empty">Ask a question to search congressional trades</div>
    </div>
  </div>

  <!-- BROWSE TAB -->
  <div id="tab-browse" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Filter Trades</div>
      <button class="btn-download" onclick="downloadBrowseCSV()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> CSV</button>
    </div>
    <div class="filters">
      <input class="filter-input" id="f-member" placeholder="Member" style="width:180px">
      <input class="filter-input" id="f-ticker" placeholder="Ticker" style="width:100px">
      <select class="filter-select" id="f-sector">
        <option value="">All Sectors</option>
        <option>Defense</option><option>Healthcare</option><option>Technology</option>
        <option>Energy</option><option>Finance</option><option>Communications</option>
        <option>Consumer</option><option>Industrials</option>
      </select>
      <select class="filter-select" id="f-type">
        <option value="">All Types</option>
        <option>Purchase</option><option>Sale</option>
      </select>
      <select class="filter-select" id="f-owner">
        <option value="">All Owners</option>
        <option>Self</option><option>Spouse</option><option>Joint</option><option>Child</option>
      </select>
      <select class="filter-select" id="f-party">
        <option value="">All Parties</option>
        <option value="Democrat">Democrat</option>
        <option value="Republican">Republican</option>
        <option value="Independent">Independent</option>
      </select>
      <select class="filter-select" id="f-chamber">
        <option value="">All Chambers</option>
        <option value="House">House</option>
        <option value="Senate">Senate</option>
      </select>
      <select class="filter-select" id="f-min-amount">
        <option value="">Any Size</option>
        <option value="15001">$15K+</option>
        <option value="50001">$50K+</option>
        <option value="100001">$100K+</option>
        <option value="250001">$250K+</option>
        <option value="500001">$500K+</option>
        <option value="1000001">$1M+</option>
        <option value="5000001">$5M+</option>
      </select>
      <button class="btn btn-primary" onclick="browseTrades()">Search</button>
    </div>
    <div class="results-section" id="browse-results">
      <div class="empty">Set filters and search</div>
    </div>
  </div>

  <!-- TIDAL TAB -->
  <div id="tab-tidal" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div class="section-title">Coverage Audit</div>
      <div>
        <button class="btn btn-primary" onclick="runTidalAudit()" style="font-size:11px;padding:10px 18px">Re-run Audit</button>
        <button class="btn btn-primary" onclick="runTidalBackfill()" style="font-size:11px;padding:10px 18px;margin-left:8px">Backfill Gaps</button>
      </div>
    </div>
    <p style="color:var(--gray);font-size:13px;margin:0 0 24px">Cross-references our DB against Unusual Whales. Any politician where UW shows more trades than we have is flagged. Click Backfill to deep-scrape the gaps from Capitol Trades.</p>
    <div id="tidal-summary" class="stats-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px">
      <div class="stat-card"><div class="stat-value" id="td-checked">-</div><div class="stat-label">UW Politicians Checked</div></div>
      <div class="stat-card"><div class="stat-value" id="td-gaps" style="color:var(--red)">-</div><div class="stat-label">Coverage Gaps</div></div>
      <div class="stat-card"><div class="stat-value" id="td-missing">-</div><div class="stat-label">Missing Trades</div></div>
      <div class="stat-card"><div class="stat-value" id="td-our">-</div><div class="stat-label">Our Members</div></div>
    </div>
    <div class="results-section" id="tidal-results">
      <div class="empty">Click Re-run Audit to scan now</div>
    </div>
  </div>


</div>

<div class="footer" style="align-items:flex-start">
  <div class="footer-text">
    <div>Subversive ETF &copy; 2026</div>
    <div style="margin-top:8px">Last scrape: <span id="footer-last-scrape">—</span></div>
    <div style="margin-top:4px"><span class="count-badge" id="footer-count">Loading...</span></div>
  </div>
  <div class="footer-sources" style="font-size:11px;opacity:0.7;line-height:1.8;text-align:right">
    <div style="font-weight:600;margin-bottom:4px;letter-spacing:1px;text-transform:uppercase">Data Sources</div>
    <div><a href="https://unusualwhales.com" target="_blank" style="color:inherit">Unusual Whales</a> — Primary live feed (API)</div>
    <div><a href="https://www.capitoltrades.com" target="_blank" style="color:inherit">Capitol Trades</a> — House &amp; Senate trade history</div>
    <div><a href="https://efdsearch.senate.gov" target="_blank" style="color:inherit">Senate eFD</a> — Official Senate disclosures</div>
    <div><a href="https://clerk.house.gov" target="_blank" style="color:inherit">House Clerk</a> — Official House PTR filings</div>
    <div><a href="https://finnhub.io" target="_blank" style="color:inherit">Finnhub</a> — Cross-reference data</div>
  </div>
</div>

<script>
const TABS = ['dashboard','recent','leaderboard','screener','query','browse','tidal'];
let _previousTab = 'dashboard';
function showTab(name) {
  _previousTab = name;
  document.querySelectorAll('.nav-tab').forEach((t,i) => {
    t.classList.toggle('active', TABS[i] === name);
  });
  TABS.forEach(n => {
    document.getElementById('tab-'+n).style.display = n === name ? 'block' : 'none';
  });
  document.getElementById('tab-profile').style.display = 'none';
  // Lazy-load data
  if (name === 'dashboard' && !window._dashLoaded) { loadDashboard(); window._dashLoaded = true; }
  if (name === 'recent' && !window._recentLoaded) { loadDropdowns(); loadRecent(); window._recentLoaded = true; }
  if (name === 'leaderboard' && !window._lbLoaded) { loadLeaderboard(); window._lbLoaded = true; }
  if (name === 'screener' && !window._scLoaded) { loadScreener(); window._scLoaded = true; }
  if (name === 'tidal' && !window._tidalLoaded) { runTidalAudit(); window._tidalLoaded = true; }
}

async function runTidalAudit() {
  const res = document.getElementById('tidal-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Cross-referencing against Unusual Whales (~30s)...</div>';
  try {
    const resp = await fetch('/api/admin/cross-source-audit?min_gap=5');
    const data = await resp.json();
    if (data.error) {
      res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Audit failed: ' + escapeHtml(data.error) + '</div>';
      return;
    }
    document.getElementById('td-checked').textContent = (data.checked_uw_politicians || 0).toLocaleString();
    document.getElementById('td-gaps').textContent = (data.gaps_found || 0).toLocaleString();
    document.getElementById('td-missing').textContent = (data.total_missing_trades || 0).toLocaleString();
    document.getElementById('td-our').textContent = (data.our_members || 0).toLocaleString();
    if (!data.gaps || data.gaps.length === 0) {
      res.innerHTML = '<div class="answer" style="border-left-color:var(--green);background:#f0fff4">✓ No gaps detected. Our DB matches Unusual Whales coverage for all checked politicians.</div>';
      return;
    }
    let h = '<table><thead><tr><th>POLITICIAN</th><th>CHAMBER</th><th>STATE</th><th>UW HAS</th><th>WE HAVE</th><th>GAP</th><th></th></tr></thead><tbody>';
    for (const g of data.gaps) {
      const link = g.our_name ? '<a href="#" onclick="showProfile(\\'' + escapeHtml(g.our_name) + '\\');return false">' + escapeHtml(g.our_name) + '</a>' : '<span style="color:var(--gray)">NOT IN DB</span>';
      h += '<tr>';
      h += '<td><strong>' + escapeHtml(g.uw_name) + '</strong><br><span style="font-size:10px;color:var(--gray)">our: ' + link + '</span></td>';
      h += '<td>' + escapeHtml(g.chamber || '') + '</td>';
      h += '<td>' + escapeHtml(g.state || '') + '</td>';
      h += '<td style="text-align:right">' + g.uw_count.toLocaleString() + '</td>';
      h += '<td style="text-align:right">' + g.our_count.toLocaleString() + '</td>';
      h += '<td style="text-align:right;color:var(--red);font-weight:600">-' + g.gap.toLocaleString() + '</td>';
      h += '<td style="text-align:right;font-size:10px;color:var(--gray)">' + escapeHtml(g.district || '') + '</td>';
      h += '</tr>';
    }
    h += '</tbody></table>';
    res.innerHTML = '<div style="margin-bottom:12px"><span class="count-badge">' + data.gaps.length + ' Gaps</span> <span style="color:var(--gray);font-size:11px;margin-left:12px">' + data.total_missing_trades.toLocaleString() + ' missing trades total</span></div>' + h;
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Audit error: ' + escapeHtml(e.message) + '</div>';
  }
}

async function runTidalBackfill() {
  const res = document.getElementById('tidal-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Backfill kicked off in background. This runs Capitol Trades deep-scrape for every gap (5-15 min). Re-run Audit in ~10 minutes to verify.</div>';
  try {
    await fetch('/api/admin/backfill-discrepancies', {method:'POST'});
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Backfill error: ' + escapeHtml(e.message) + '</div>';
  }
}

function showProfile(memberName) {
  TABS.forEach(n => { document.getElementById('tab-'+n).style.display = 'none'; });
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-profile').style.display = 'block';
  loadMemberProfile(memberName);
}

function backFromProfile() {
  document.getElementById('tab-profile').style.display = 'none';
  showTab(_previousTab);
}

// Load dropdowns and recent trades on page load
async function loadDropdowns() {
  try {
    const [membersResp, tickersResp] = await Promise.all([
      fetch('/api/filters/members'),
      fetch('/api/filters/tickers')
    ]);
    const members = await membersResp.json();
    const tickers = await tickersResp.json();
    const mSel = document.getElementById('r-member');
    members.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; mSel.appendChild(o); });
    const tSel = document.getElementById('r-ticker');
    tickers.forEach(t => { const o = document.createElement('option'); o.value = t; o.textContent = t; tSel.appendChild(o); });
  } catch(e) { console.error('Failed to load filters:', e); }
}

async function loadRecent() {
  try {
    const [tradesResp, statsResp] = await Promise.all([
      fetch('/api/trades/recent?limit=50'),
      fetch('/api/stats')
    ]);
    const trades = await tradesResp.json();
    const stats = await statsResp.json();
    const res = document.getElementById('recent-results');
    const badge = document.getElementById('stats-badge');
    if (stats.total_trades) {
      badge.textContent = stats.total_trades.toLocaleString() + ' Trades tracked';
      badge.style.display = 'inline-block';
      document.getElementById('footer-count').textContent = stats.total_trades.toLocaleString() + ' Trades';
    }
    if (trades.length === 0) {
      res.innerHTML = '<div class="empty">No trades found yet — trigger a scrape to load data</div>';
    } else {
      res.innerHTML = renderTradeTable(trades);
    }
  } catch(e) {
    document.getElementById('recent-results').innerHTML = '<div class="answer" style="border-left-color:var(--red)">Failed to load: ' + e.message + '</div>';
  }
}

async function filterRecent() {
  const member = document.getElementById('r-member').value;
  const ticker = document.getElementById('r-ticker').value;
  const txType = document.getElementById('r-type').value;
  const party = document.getElementById('r-party').value;
  const chamber = document.getElementById('r-chamber').value;
  const minAmt = document.getElementById('r-min-amount').value;
  const params = new URLSearchParams();
  if (member) params.set('member', member);
  if (ticker) params.set('ticker', ticker);
  if (txType) params.set('transaction_type', txType);
  if (party) params.set('party', party);
  if (chamber) params.set('chamber', chamber);
  if (minAmt) params.set('min_amount', minAmt);
  // No limit — return every match. The user explicitly does not want a cap.
  params.set('limit', '100000');
  const res = document.getElementById('recent-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Filtering...</div>';
  try {
    const resp = await fetch('/api/trades/recent?' + params.toString());
    const trades = await resp.json();
    if (trades.length === 0) {
      res.innerHTML = '<div class="empty">No trades match those filters</div>';
    } else {
      res.innerHTML = '<div style="margin-bottom:12px"><span class="count-badge">' + trades.length + ' Results</span></div>' + renderTradeTable(trades);
    }
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Error: ' + e.message + '</div>';
  }
}

function clearFilters() {
  document.getElementById('r-member').value = '';
  document.getElementById('r-ticker').value = '';
  document.getElementById('r-type').value = '';
  document.getElementById('r-party').value = '';
  document.getElementById('r-chamber').value = '';
  document.getElementById('r-min-amount').value = '';
  loadRecent();
}

async function loadFooter() {
  try {
    const h = await (await fetch('/api/health')).json();
    if (h.total_trades) document.getElementById('footer-count').textContent = h.total_trades.toLocaleString() + ' Trades';
    const el = document.getElementById('footer-last-scrape');
    if (h.last_scrape_at) {
      const d = new Date(h.last_scrape_at + 'Z');
      el.textContent = d.toLocaleString(undefined, {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) +
        (h.last_scrape_new_trades != null ? ' (+' + h.last_scrape_new_trades + ' new)' : '');
    } else {
      el.textContent = 'in progress…';
    }
  } catch(e) { /* leave defaults */ }
}

document.addEventListener('DOMContentLoaded', () => { loadDashboard(); loadFooter(); window._dashLoaded = true; });

function setQ(q) {
  document.getElementById('question').value = q;
  askQuestion();
}

async function askQuestion() {
  const q = document.getElementById('question').value.trim();
  if (!q) return;
  const btn = document.getElementById('ask-btn');
  const res = document.getElementById('results');
  btn.disabled = true;
  btn.textContent = 'WORKING...';
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Analyzing trades...</div>';

  try {
    const resp = await fetch('/api/query', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || ('Server error ' + resp.status));
    }
    const data = await resp.json();
    let html = '';
    if (data.error) {
      html = '<div class="answer" style="border-left-color:var(--red)">Error: ' + data.error + '</div>';
    } else {
      if (data.answer) {
        html += '<div class="answer">' + formatMarkdown(data.answer) + '</div>';
      }
      if (data.sql) {
        html += '<div class="sql-block">' + escapeHtml(data.sql) + '</div>';
      }
      if (data.results && data.results.length > 0) {
        html += renderTable(data.results);
      }
    }
    res.innerHTML = html;
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Request failed: ' + e.message + '</div>';
  }
  btn.disabled = false;
  btn.textContent = 'ASK';
}

async function browseTrades() {
  const params = new URLSearchParams();
  const m = document.getElementById('f-member').value.trim();
  const t = document.getElementById('f-ticker').value.trim();
  const s = document.getElementById('f-sector').value;
  const tt = document.getElementById('f-type').value;
  const ow = document.getElementById('f-owner').value;
  const pt = document.getElementById('f-party').value;
  const ch = document.getElementById('f-chamber').value;
  const ma = document.getElementById('f-min-amount').value;
  if (m) params.set('member', m);
  if (t) params.set('ticker', t);
  if (s) params.set('sector', s);
  if (tt) params.set('transaction_type', tt);
  if (ow) params.set('owner', ow);
  if (pt) params.set('party', pt);
  if (ch) params.set('chamber', ch);
  if (ma) params.set('min_amount', ma);
  params.set('limit', '50');

  const res = document.getElementById('browse-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Loading...</div>';
  try {
    const resp = await fetch('/api/trades?' + params.toString());
    const data = await resp.json();
    if (data.length === 0) {
      res.innerHTML = '<div class="empty">No trades found</div>';
    } else {
      res.innerHTML = '<div style="margin-bottom:12px"><span class="count-badge">' + data.length + ' Results</span></div>' + renderTradeTable(data);
    }
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Error: ' + e.message + '</div>';
  }
}

function renderTable(rows) {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  let h = '<div style="overflow-x:auto"><table class="trades-table"><thead><tr>';
  cols.forEach(c => h += '<th>' + c.replace(/_/g,' ') + '</th>');
  h += '</tr></thead><tbody>';
  rows.slice(0, 50).forEach(r => {
    h += '<tr>';
    cols.forEach(c => {
      let v = r[c] ?? '';
      if (typeof v === 'number' && v > 999) v = '$' + v.toLocaleString();
      if (typeof v === 'string' && v.includes('00:00:00')) v = v.split(' ')[0];
      h += '<td>' + escapeHtml(String(v)) + '</td>';
    });
    h += '</tr>';
  });
  h += '</tbody></table></div>';
  return h;
}

function partyBadge(party) {
  if (!party) return '';
  const p = party.charAt(0).toUpperCase();
  const cls = p === 'R' ? 'party-r' : p === 'D' ? 'party-d' : 'party-i';
  return '<span class="'+cls+'">('+p+')</span>';
}

function renderTradeTable(trades) {
  let h = '<div style="overflow-x:auto"><table class="trades-table"><thead><tr>';
  h += '<th>Member</th><th>Type</th><th>Ticker</th><th>Asset</th><th>Amount</th><th>Owner</th><th>Trade Date</th><th>Filed</th><th>Sector</th>';
  h += '</tr></thead><tbody>';
  trades.forEach(t => {
    const type = t.transaction_type || '';
    const badge = type.includes('Purchase') ? 'badge-buy' : type.includes('Sale') ? 'badge-sell' : 'badge-other';
    const amt = t.amount_low ? ('$' + t.amount_low.toLocaleString() + (t.amount_high ? ' - $' + t.amount_high.toLocaleString() : '+')) : 'N/A';
    const txDate = t.transaction_date ? t.transaction_date.split('T')[0] : 'N/A';
    const fileDate = t.filing_date ? t.filing_date.split('T')[0] : '-';
    h += '<tr>';
    h += '<td><strong class="member-link" onclick="showProfile(\\'' + escapeHtml(t.member_name) + '\\')">' + escapeHtml(t.member_name) + '</strong>' + partyBadge(t.party) + '</td>';
    h += '<td><span class="badge ' + badge + '">' + escapeHtml(type) + '</span></td>';
    h += '<td><strong>' + escapeHtml(t.ticker || 'N/A') + '</strong></td>';
    h += '<td>' + escapeHtml((t.asset_description || '').substring(0, 40)) + '</td>';
    h += '<td>' + amt + '</td>';
    h += '<td>' + escapeHtml(t.owner || '') + '</td>';
    h += '<td>' + txDate + '</td>';
    h += '<td style="color:var(--gray-light);font-size:11px">' + fileDate + '</td>';
    h += '<td>' + escapeHtml(t.sector || '') + '</td>';
    h += '</tr>';
  });
  h += '</tbody></table></div>';
  return h;
}

// ---- DASHBOARD ----
async function loadDashboard() {
  try {
    const resp = await fetch('/api/dashboard');
    const d = await resp.json();
    document.getElementById('ds-total').textContent = d.total_trades.toLocaleString();
    document.getElementById('ds-filing').textContent = d.filing_members + ' / ' + d.congress_total;
    document.getElementById('ds-members').textContent = d.total_members.toLocaleString();
    document.getElementById('ds-buys').textContent = d.buy_count.toLocaleString();
    document.getElementById('ds-sells').textContent = d.sell_count.toLocaleString();
    const badge = document.getElementById('dash-badge');
    badge.textContent = d.total_trades.toLocaleString() + ' Trades';
    badge.style.display = 'inline-block';
    document.getElementById('footer-count').textContent = d.total_trades.toLocaleString() + ' Trades';

    // Top tickers bar chart
    const maxTk = d.top_tickers.length ? Math.max(...d.top_tickers.map(t => t.trade_count)) : 1;
    let tkHtml = '<div style="margin-top:8px">';
    d.top_tickers.forEach(t => {
      const pct = Math.round(t.trade_count / maxTk * 100);
      const buyPct = t.trade_count > 0 ? Math.round(t.buys / t.trade_count * 100) : 0;
      tkHtml += '<div class="bar-row">';
      tkHtml += '<div class="bar-label"><strong class="member-link" onclick="showScreenerTicker(\\''+escapeHtml(t.ticker)+'\\')">'+escapeHtml(t.ticker)+'</strong></div>';
      tkHtml += '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:linear-gradient(90deg,var(--green) '+buyPct+'%,var(--red) '+buyPct+'%)"></div></div>';
      tkHtml += '<div class="bar-count">'+t.trade_count+'</div>';
      tkHtml += '</div>';
    });
    tkHtml += '</div>';
    document.getElementById('dash-tickers').innerHTML = d.top_tickers.length ? tkHtml : '<div class="empty">No trades in last 30 days</div>';

    // Top members
    const maxMb = d.top_members.length ? Math.max(...d.top_members.map(m => m.trade_count)) : 1;
    let mbHtml = '<div style="margin-top:8px">';
    d.top_members.forEach(m => {
      const pct = Math.round(m.trade_count / maxMb * 100);
      const partyColor = m.party === 'Republican' ? '#c0392b' : m.party === 'Democrat' ? '#2980b9' : 'var(--gray)';
      mbHtml += '<div class="bar-row">';
      mbHtml += '<div class="bar-label"><strong class="member-link" onclick="showProfile(\\''+escapeHtml(m.name)+'\\')">'+escapeHtml(m.name.split(' ').pop())+'</strong>'+partyBadge(m.party)+'</div>';
      mbHtml += '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:'+partyColor+'"></div></div>';
      mbHtml += '<div class="bar-count">'+m.trade_count+'</div>';
      mbHtml += '</div>';
    });
    mbHtml += '</div>';
    document.getElementById('dash-members').innerHTML = d.top_members.length ? mbHtml : '<div class="empty">No activity in last 30 days</div>';

    // Weekly activity
    const wkChange = d.trades_this_week - d.trades_last_week;
    const wkArrow = wkChange >= 0 ? '&#9650;' : '&#9660;';
    const wkColor = wkChange >= 0 ? 'var(--green)' : 'var(--red)';
    document.getElementById('dash-activity').innerHTML =
      '<span style="font-size:20px;font-weight:700">'+d.trades_this_week+'</span> trades this week ' +
      '<span style="color:'+wkColor+'">'+wkArrow+' '+Math.abs(wkChange)+' vs last week ('+d.trades_last_week+')</span>';
  } catch(e) {
    document.getElementById('dash-tickers').innerHTML = '<div class="answer" style="border-left-color:var(--red)">Failed to load dashboard: ' + e.message + '</div>';
  }
}

function showScreenerTicker(ticker) {
  showTab('screener');
}

// ---- LEADERBOARD ----
async function loadLeaderboard() {
  const period = document.getElementById('lb-period').value;
  const chamber = document.getElementById('lb-chamber').value;
  const params = new URLSearchParams();
  params.set('period', period);
  if (chamber) params.set('chamber', chamber);
  const res = document.getElementById('leaderboard-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Loading leaderboard...</div>';
  try {
    const resp = await fetch('/api/leaderboard?' + params.toString());
    const rows = await resp.json();
    if (!rows.length) { res.innerHTML = '<div class="empty">No data for this period</div>'; return; }
    let h = '<div style="overflow-x:auto"><table class="trades-table"><thead><tr>';
    h += '<th>#</th><th>Member</th><th>Chamber</th><th>Party</th><th>State</th><th>Trades</th><th>Buys</th><th>Sells</th><th>Est. Volume</th><th>Last Trade</th>';
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
      const partyBg = r.party === 'Republican' ? '#fce4ec' : r.party === 'Democrat' ? '#e3f2fd' : 'var(--cream-light)';
      const partyColor = r.party === 'Republican' ? '#c0392b' : r.party === 'Democrat' ? '#2980b9' : 'var(--gray)';
      h += '<tr>';
      h += '<td style="font-weight:700;color:var(--gray)">'+r.rank+'</td>';
      h += '<td><strong class="member-link" onclick="showProfile(\\''+escapeHtml(r.name)+'\\')">'+escapeHtml(r.name)+'</strong>'+partyBadge(r.party)+'</td>';
      h += '<td>'+escapeHtml(r.chamber)+'</td>';
      h += '<td><span style="background:'+partyBg+';color:'+partyColor+';padding:2px 8px;font-size:11px;font-family:IBM Plex Mono,monospace;font-weight:600">'+escapeHtml(r.party)+'</span></td>';
      h += '<td>'+escapeHtml(r.state)+'</td>';
      h += '<td><strong>'+r.trade_count+'</strong></td>';
      h += '<td style="color:var(--green)">'+r.buys+'</td>';
      h += '<td style="color:var(--red)">'+r.sells+'</td>';
      h += '<td>$'+r.total_volume.toLocaleString()+'</td>';
      h += '<td>'+(r.last_trade_date ? r.last_trade_date.split('T')[0] : 'N/A')+'</td>';
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    res.innerHTML = '<div style="margin-bottom:12px"><span class="count-badge">'+rows.length+' Members</span></div>' + h;
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Error: ' + e.message + '</div>';
  }
}

// ---- MEMBER PROFILE ----
async function loadMemberProfile(name) {
  const res = document.getElementById('profile-content');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Loading profile...</div>';
  try {
    const resp = await fetch('/api/members/' + encodeURIComponent(name) + '/profile');
    const p = await resp.json();
    if (resp.status === 404) { res.innerHTML = '<div class="empty">Member not found</div>'; return; }

    const partyColor = p.party === 'Republican' ? '#c0392b' : p.party === 'Democrat' ? '#2980b9' : 'var(--gray)';

    let h = '<div class="profile-header">';
    h += '<div><div class="profile-name">'+escapeHtml(p.name)+'</div>';
    h += '<div class="profile-meta"><span style="color:'+partyColor+';font-weight:600">'+escapeHtml(p.party)+'</span> &middot; '+escapeHtml(p.chamber)+' &middot; '+escapeHtml(p.state)+'</div>';
    h += '</div></div>';

    h += '<div class="profile-stats">';
    h += '<div class="stat-card"><div class="stat-value">'+p.total_trades+'</div><div class="stat-label">Total Trades</div></div>';
    h += '<div class="stat-card"><div class="stat-value" style="color:var(--green)">'+p.buys+'</div><div class="stat-label">Purchases</div></div>';
    h += '<div class="stat-card"><div class="stat-value" style="color:var(--red)">'+p.sells+'</div><div class="stat-label">Sales</div></div>';
    h += '<div class="stat-card"><div class="stat-value">$'+p.total_volume.toLocaleString()+'</div><div class="stat-label">Est. Volume</div></div>';
    h += '</div>';

    // Top tickers + sectors side by side
    h += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-bottom:32px">';

    // Top tickers
    h += '<div><div class="section-title">Top Tickers</div>';
    if (p.top_tickers.length) {
      const maxTk = Math.max(...p.top_tickers.map(t => t.count));
      p.top_tickers.forEach(t => {
        const pct = Math.round(t.count / maxTk * 100);
        h += '<div class="bar-row"><div class="bar-label"><strong>'+escapeHtml(t.ticker)+'</strong></div>';
        h += '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:var(--black)"></div></div>';
        h += '<div class="bar-count">'+t.count+'</div></div>';
      });
    } else { h += '<div class="empty">No ticker data</div>'; }
    h += '</div>';

    // Sectors
    h += '<div><div class="section-title">Sector Breakdown</div>';
    if (p.sectors.length) {
      const maxSc = Math.max(...p.sectors.map(s => s.count));
      p.sectors.slice(0, 8).forEach(s => {
        const pct = Math.round(s.count / maxSc * 100);
        h += '<div class="bar-row"><div class="bar-label" style="width:100px">'+escapeHtml(s.sector.substring(0,12))+'</div>';
        h += '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%;background:var(--cream-dark)"></div></div>';
        h += '<div class="bar-count">'+s.count+'</div></div>';
      });
    } else { h += '<div class="empty">No sector data</div>'; }
    h += '</div></div>';

    // Recent trades
    h += '<div class="section-title">Trade History</div>';
    if (p.trades.length) {
      h += renderTradeTable(p.trades);
    } else { h += '<div class="empty">No trades found</div>'; }

    res.innerHTML = h;
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Error: ' + e.message + '</div>';
  }
}

// ---- SCREENER ----
async function loadScreener() {
  const period = document.getElementById('sc-period').value;
  const res = document.getElementById('screener-results');
  res.innerHTML = '<div class="loading"><span class="spinner"></span> Loading screener...</div>';
  try {
    const resp = await fetch('/api/screener?period=' + period);
    const rows = await resp.json();
    if (!rows.length) { res.innerHTML = '<div class="empty">No data for this period</div>'; return; }
    let h = '<div style="overflow-x:auto"><table class="trades-table"><thead><tr>';
    h += '<th>Ticker</th><th>Sector</th><th>Signal</th><th>Score</th><th>Trades</th><th>Members</th><th>Buys</th><th>Sells</th><th>Est. Volume</th><th>Last Trade</th>';
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
      const sigMap = {'STRONG BUY':'signal-strong-buy','BUY':'signal-buy','SELL':'signal-sell','STRONG SELL':'signal-strong-sell','MIXED':'signal-mixed'};
      const sigClass = sigMap[r.signal] || 'signal-mixed';
      h += '<tr>';
      h += '<td><strong>'+escapeHtml(r.ticker)+'</strong></td>';
      h += '<td>'+escapeHtml(r.sector)+'</td>';
      h += '<td><span class="'+sigClass+'">'+r.signal+'</span></td>';
      const scoreColor = r.score > 0 ? 'var(--green)' : r.score < 0 ? 'var(--red)' : 'var(--gray)';
      h += '<td style="color:'+scoreColor+';font-weight:700">'+(r.score > 0 ? '+' : '')+r.score+'</td>';
      h += '<td><strong>'+r.trade_count+'</strong></td>';
      h += '<td>'+r.unique_members+'</td>';
      h += '<td style="color:var(--green)">'+r.buys+'</td>';
      h += '<td style="color:var(--red)">'+r.sells+'</td>';
      h += '<td>$'+r.total_volume.toLocaleString()+'</td>';
      h += '<td>'+(r.last_trade_date ? r.last_trade_date.split('T')[0] : 'N/A')+'</td>';
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    res.innerHTML = '<div style="margin-bottom:12px"><span class="count-badge">'+rows.length+' Stocks</span></div>' + h;
  } catch(e) {
    res.innerHTML = '<div class="answer" style="border-left-color:var(--red)">Error: ' + e.message + '</div>';
  }
}

function formatMarkdown(text) {
  return text
    .replace(/\\n/g, '\\n')
    .replace(/## \\*\\*(.*?)\\*\\*/g, '<h3>$1</h3>')
    .replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*(.*?)\\*/g, '<em>$1</em>')
    .replace(/^- (.*)/gm, '<li>$1</li>')
    .replace(/\\u2022 (.*)/g, '<li>$1</li>')
    .replace(/\\n/g, '<br>');
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---- CSV DOWNLOAD ----
function escapeCsvField(val) {
  if (val === null || val === undefined) return '';
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function downloadCSV(rows, headers, filename) {
  let csv = headers.map(escapeCsvField).join(',') + '\\n';
  rows.forEach(r => {
    csv += headers.map(h => escapeCsvField(r[h] ?? '')).join(',') + '\\n';
  });
  const blob = new Blob([csv], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// Store fetched data for CSV export
window._csvData = {};

async function downloadDashboardCSV() {
  try {
    // Export all trades as CSV
    const resp = await fetch('/api/trades?limit=50000');
    const trades = await resp.json();
    const headers = ['member_name','party','chamber','transaction_type','ticker','asset_description','amount_low','amount_high','transaction_date','filing_date','owner','sector','industry'];
    downloadCSV(trades, headers, 'subversive-all-trades.csv');
  } catch(e) { alert('Failed to export: ' + e.message); }
}

async function downloadRecentCSV() {
  try {
    const member = document.getElementById('r-member').value;
    const ticker = document.getElementById('r-ticker').value;
    const txType = document.getElementById('r-type').value;
    const params = new URLSearchParams();
    if (member) params.set('member', member);
    if (ticker) params.set('ticker', ticker);
    if (txType) params.set('transaction_type', txType);
    params.set('limit', '10000');
    const resp = await fetch('/api/trades/recent?' + params.toString());
    const trades = await resp.json();
    const headers = ['member_name','party','chamber','transaction_type','ticker','asset_description','amount_low','amount_high','transaction_date','filing_date','owner','sector','industry'];
    downloadCSV(trades, headers, 'subversive-recent-trades.csv');
  } catch(e) { alert('Failed to export: ' + e.message); }
}

async function downloadLeaderboardCSV() {
  try {
    const period = document.getElementById('lb-period').value;
    const chamber = document.getElementById('lb-chamber').value;
    const params = new URLSearchParams();
    params.set('period', period);
    if (chamber) params.set('chamber', chamber);
    const resp = await fetch('/api/leaderboard?' + params.toString());
    const rows = await resp.json();
    const headers = ['rank','name','chamber','party','state','trade_count','buys','sells','total_volume','last_trade_date'];
    downloadCSV(rows, headers, 'subversive-leaderboard.csv');
  } catch(e) { alert('Failed to export: ' + e.message); }
}

async function downloadScreenerCSV() {
  try {
    const period = document.getElementById('sc-period').value;
    const resp = await fetch('/api/screener?period=' + period);
    const rows = await resp.json();
    const headers = ['ticker','sector','signal','score','trade_count','unique_members','buys','sells','total_volume','last_trade_date'];
    downloadCSV(rows, headers, 'subversive-screener.csv');
  } catch(e) { alert('Failed to export: ' + e.message); }
}

async function downloadBrowseCSV() {
  try {
    const params = new URLSearchParams();
    const m = document.getElementById('f-member').value.trim();
    const t = document.getElementById('f-ticker').value.trim();
    const s = document.getElementById('f-sector').value;
    const tt = document.getElementById('f-type').value;
    const ow = document.getElementById('f-owner').value;
    const pt = document.getElementById('f-party').value;
    const ch = document.getElementById('f-chamber').value;
    const ma = document.getElementById('f-min-amount').value;
    if (m) params.set('member', m);
    if (t) params.set('ticker', t);
    if (s) params.set('sector', s);
    if (tt) params.set('transaction_type', tt);
    if (ow) params.set('owner', ow);
    if (pt) params.set('party', pt);
    if (ch) params.set('chamber', ch);
    if (ma) params.set('min_amount', ma);
    params.set('limit', '10000');
    const resp = await fetch('/api/trades?' + params.toString());
    const trades = await resp.json();
    const headers = ['member_name','party','chamber','transaction_type','ticker','asset_description','amount_low','amount_high','transaction_date','filing_date','owner','sector','industry'];
    downloadCSV(trades, headers, 'subversive-browse-trades.csv');
  } catch(e) { alert('Failed to export: ' + e.message); }
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
