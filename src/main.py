"""Main FastAPI application."""

import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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
    title="Nancy the Ripper",
    description="Congressional stock trade tracker with NL queries and email alerts",
    version="0.1.0",
)

app.include_router(router)


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
        from src.scheduler.jobs import run_email_job, run_scrape_job

        global _scheduler
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            run_scrape_job,
            "interval",
            hours=settings.scrape_interval_hours,
            id="scrape_job",
            name="Scrape congressional disclosures",
        )
        _scheduler.add_job(
            run_email_job,
            "cron",
            hour=settings.email_hour,
            id="email_job",
            name="Send daily trade email",
        )
        _scheduler.start()
        logger.info(
            f"Scheduler started: scraping every {settings.scrape_interval_hours}h, "
            f"emails at {settings.email_hour}:00 UTC"
        )


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
<title>Nancy the Ripper — Congressional Trade Tracker</title>
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
</style>
</head>
<body>

<div class="header">
  <h1>NANCY <span>THE RIPPER</span></h1>
  <div class="header-meta">Congressional Trade Tracker</div>
</div>

<div class="nav-bar">
  <div class="nav-tab active" onclick="showTab('recent')">Recent</div>
  <div class="nav-tab" onclick="showTab('query')">Query</div>
  <div class="nav-tab" onclick="showTab('browse')">Browse</div>
  <div class="nav-tab" onclick="showTab('subscribe')">Alerts</div>
</div>

<div class="container">

  <!-- RECENT TRADES TAB -->
  <div id="tab-recent">
    <div class="section-title">Latest Congressional Trades <span id="stats-badge" class="count-badge" style="display:none"></span></div>
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
    <div class="section-title">Filter Trades</div>
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
      <button class="btn btn-primary" onclick="browseTrades()">Search</button>
    </div>
    <div class="results-section" id="browse-results">
      <div class="empty">Set filters and search</div>
    </div>
  </div>

  <!-- SUBSCRIBE TAB -->
  <div id="tab-subscribe" style="display:none">
    <div class="section-title">Email Notifications</div>
    <div class="subscribe-card">
      <h2>Daily Alerts</h2>
      <p>Get notified every morning when congress members disclose new stock trades.</p>
      <div class="query-row">
        <input class="query-input" id="sub-email" placeholder="your@email.com">
        <button class="btn btn-primary" onclick="subscribe()">Subscribe</button>
      </div>
      <div id="sub-result" style="margin-top:14px;font-family:'IBM Plex Mono',monospace;font-size:13px"></div>
    </div>
  </div>

</div>

<div class="footer">
  <div class="footer-text">Nancy the Ripper &copy; 2026</div>
  <div class="footer-text">Data from Senate &amp; Capitol Trades<span class="count-badge" id="footer-count">Loading...</span></div>
</div>

<script>
const TABS = ['recent','query','browse','subscribe'];
function showTab(name) {
  document.querySelectorAll('.nav-tab').forEach((t,i) => {
    t.classList.toggle('active', TABS[i] === name);
  });
  TABS.forEach(n => {
    document.getElementById('tab-'+n).style.display = n === name ? 'block' : 'none';
  });
}

// Load recent trades and stats on page load
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
document.addEventListener('DOMContentLoaded', loadRecent);

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
  if (m) params.set('member', m);
  if (t) params.set('ticker', t);
  if (s) params.set('sector', s);
  if (tt) params.set('transaction_type', tt);
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

async function subscribe() {
  const email = document.getElementById('sub-email').value.trim();
  if (!email) return;
  try {
    const resp = await fetch('/api/subscribe', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email})
    });
    const data = await resp.json();
    document.getElementById('sub-result').innerHTML = '<span style="color:var(--green)">&#10003; ' + data.message + '</span>';
  } catch(e) {
    document.getElementById('sub-result').innerHTML = '<span style="color:var(--red)">Failed: ' + e.message + '</span>';
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

function renderTradeTable(trades) {
  let h = '<div style="overflow-x:auto"><table class="trades-table"><thead><tr>';
  h += '<th>Member</th><th>Type</th><th>Ticker</th><th>Asset</th><th>Amount</th><th>Date</th><th>Sector</th>';
  h += '</tr></thead><tbody>';
  trades.forEach(t => {
    const type = t.transaction_type || '';
    const badge = type.includes('Purchase') ? 'badge-buy' : type.includes('Sale') ? 'badge-sell' : 'badge-other';
    const amt = t.amount_low ? ('$' + t.amount_low.toLocaleString() + (t.amount_high ? ' - $' + t.amount_high.toLocaleString() : '+')) : 'N/A';
    const date = t.transaction_date ? t.transaction_date.split('T')[0] : 'N/A';
    h += '<tr>';
    h += '<td><strong>' + escapeHtml(t.member_name) + '</strong></td>';
    h += '<td><span class="badge ' + badge + '">' + escapeHtml(type) + '</span></td>';
    h += '<td><strong>' + escapeHtml(t.ticker || 'N/A') + '</strong></td>';
    h += '<td>' + escapeHtml((t.asset_description || '').substring(0, 40)) + '</td>';
    h += '<td>' + amt + '</td>';
    h += '<td>' + date + '</td>';
    h += '<td>' + escapeHtml(t.sector || '') + '</td>';
    h += '</tr>';
  });
  h += '</tbody></table></div>';
  return h;
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
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
