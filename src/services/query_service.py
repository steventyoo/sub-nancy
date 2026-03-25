"""Claude-powered natural language query engine for congressional trades."""

import json
import logging
import re

import anthropic
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import settings

logger = logging.getLogger(__name__)

DB_SCHEMA = """
Tables:
1. members (id, name, chamber, state, district, party, active)
2. trades (id, member_id FK->members.id, transaction_date, filing_date, ticker, asset_description,
   asset_type, transaction_type, amount_low, amount_high, owner, sector, industry, source_url,
   raw_filing_url, created_at)
3. sectors (id, ticker, sector, industry, company_name)

Key values:
- chamber: 'House' or 'Senate'
- transaction_type: 'Purchase', 'Sale', 'Sale (Full)', 'Sale (Partial)', 'Exchange'
- owner: 'Self', 'Spouse', 'Child', 'Joint'
- sector examples: 'Defense', 'Healthcare', 'Technology', 'Energy', 'Finance',
  'Communications', 'Consumer', 'Industrials', 'Real Estate', 'Utilities'
- amount ranges are stored as numeric bounds (amount_low, amount_high)
  e.g. $1,001-$15,000 → amount_low=1001, amount_high=15000

Dates are stored as timestamp/datetime. Use CURRENT_DATE and INTERVAL for comparisons.
The database is PostgreSQL.
"""

SYSTEM_PROMPT = f"""You are a SQL query generator for a congressional stock trade database.

{DB_SCHEMA}

Given a natural language question, generate a PostgreSQL SELECT query to answer it.
Return ONLY a JSON object with two keys:
- "sql": the SQL query string (SELECT only, no mutations)
- "explanation": a brief explanation of what the query does

Rules:
- ONLY generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, etc.
- Always JOIN members and trades when member info is needed
- Use ILIKE for case-insensitive name matching
- For date ranges, interpret relative terms like "last month", "this year", "prior to" etc.
  Use CURRENT_DATE for current date and INTERVAL for offsets (e.g. CURRENT_DATE - INTERVAL '30 days').
- When asked about sectors like "defense stocks", filter on trades.sector or join with sectors table
- Limit results to 50 unless the user asks for more
- Order by transaction_date DESC by default
"""


async def natural_language_query(db: Session, question: str) -> dict:
    """Take a natural language question, generate SQL via Claude, execute it, and return results."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Step 1: Generate SQL from the question
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )

    response_text = response.content[0].text.strip()

    # Parse JSON from response (handle markdown code blocks)
    json_match = re.search(r"\{[\s\S]*\}", response_text)
    if not json_match:
        return {"error": "Failed to generate SQL query", "raw_response": response_text}

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError:
        return {"error": "Failed to parse SQL response", "raw_response": response_text}

    sql = parsed.get("sql", "")
    explanation = parsed.get("explanation", "")

    # Safety check: only allow SELECT
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return {"error": "Only SELECT queries are allowed", "generated_sql": sql}

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "EXEC"]
    for word in forbidden:
        if re.search(rf"\b{word}\b", sql_upper):
            return {"error": f"Forbidden SQL operation: {word}", "generated_sql": sql}

    # Step 2: Execute the query
    try:
        result = db.execute(text(sql))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception as e:
        logger.error(f"SQL execution failed: {e}\nQuery: {sql}")
        return {"error": f"Query execution failed: {str(e)}", "generated_sql": sql}

    # Step 3: Format results with Claude
    if not rows:
        return {
            "question": question,
            "sql": sql,
            "explanation": explanation,
            "answer": "No results found for this query.",
            "results": [],
        }

    # For large result sets, just return the data
    if len(rows) > 20:
        return {
            "question": question,
            "sql": sql,
            "explanation": explanation,
            "answer": f"Found {len(rows)} results.",
            "results": rows,
        }

    # For smaller sets, ask Claude to summarize
    format_response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    f"The user asked: \"{question}\"\n\n"
                    f"Query results ({len(rows)} rows):\n{json.dumps(rows, default=str, indent=2)}\n\n"
                    "Provide a concise, readable summary of these results. "
                    "Use bullet points for multiple items. Include key details like names, "
                    "tickers, amounts, and dates."
                ),
            }
        ],
    )

    return {
        "question": question,
        "sql": sql,
        "explanation": explanation,
        "answer": format_response.content[0].text,
        "results": rows,
    }
