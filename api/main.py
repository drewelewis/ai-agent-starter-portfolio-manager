"""
Trading Platform FastAPI
REST API that wraps the TradingPlatformAgent for portfolio event ledger operations.

Endpoints:
    GET  /health                                        - Service health + DB connectivity
    GET  /                                              - API info and endpoint map
    POST /chat                                          - Natural-language chat with the agent
    POST /clear_session                                 - Clear conversation history for a session
    GET  /accounts                                      - List all distinct account IDs
    GET  /portfolio/summary/all                         - Net positions for ALL accounts (one query)
    GET  /portfolio/{account_id}                        - Portfolio summary for one account
    GET  /portfolio/{account_id}/trades                 - Trade history (BUY/SELL) for an account
    GET  /portfolio/{account_id}/events                 - All events for an account
    GET  /portfolio/{account_id}/{ticker_symbol}/events - Events for a specific account+ticker
    GET  /ticker/{ticker_symbol}/price                  - Latest observed price for a ticker
    GET  /ticker/{ticker_symbol}/events                 - All events for a ticker
    POST /events                                        - Insert a new portfolio event
    POST /query                                         - Execute a read-only SQL SELECT
    GET  /agent/status                                  - Agent tools and capabilities
"""

import os
from typing import Dict, Optional, Any
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from agent_framework import AgentThread

from agents.trading_platform_agent import create_trading_platform_agent
from operations.trading_platform_operations import TradingPlatformOperations

load_dotenv(override=True)

# ── Service metadata ───────────────────────────────────────────────────────────
SERVICE_NAME    = os.getenv("SERVICE_NAME",    "trading-platform-api")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")
# Set SERVER_URL in env to the public base URL (no trailing slash) so Swagger
# UI sends requests to the right host instead of localhost.
# e.g. SERVER_URL=https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io
SERVER_URL      = os.getenv("SERVER_URL", "")

# Build servers list for Swagger UI
_servers = []
if SERVER_URL:
    _servers.append({"url": SERVER_URL, "description": "Production"})
_servers.append({"url": "/", "description": "Current host"})

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Trading Platform API",
    description=(
        "AI-powered portfolio event ledger API. "
        "Chat naturally with the Trading Platform Agent or call structured endpoints directly."
    ),
    version=SERVICE_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    servers=_servers,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global singletons ──────────────────────────────────────────────────────────
agent       = None                          # TradingPlatformAgent (ChatAgent)
db_ops      = TradingPlatformOperations()   # Direct DB access for structured endpoints
sessions: Dict[str, AgentThread] = {}       # session_id -> AgentThread


# ── Request / response models ──────────────────────────────────────────────────
class ChatRequest(BaseModel):
    session_id: str  = Field(..., description="Unique session identifier")
    message:    str  = Field(..., description="Natural-language message for the agent")


class ChatResponse(BaseModel):
    session_id: str
    response:   str
    agent:      str = "TradingPlatformAgent"


class ClearSessionRequest(BaseModel):
    session_id: str = Field(..., description="Session to clear")


class InsertEventRequest(BaseModel):
    account_id:      str   = Field(..., description="Account ID (e.g. A100)")
    ticker_symbol:   str   = Field(..., description="Ticker symbol (e.g. MSFT)")
    event_ts:        str   = Field(..., description="ISO 8601 UTC timestamp")
    event_type:      str   = Field(..., description="BUY, SELL, or PRICE")
    shares:          float = Field(..., description="Number of shares (0 for PRICE events)")
    price_per_share: float = Field(..., description="Trade or market price")
    currency:        str   = Field("USD", description="ISO currency code")
    source:          str   = Field("api",  description="Origin of the event")


class QueryRequest(BaseModel):
    sql: str = Field(
        ...,
        description="A read-only PostgreSQL SELECT statement against portfolio_event_ledger",
        example="SELECT account_id, COUNT(*) AS trades FROM portfolio_event_ledger WHERE event_type IN ('BUY','SELL') GROUP BY account_id ORDER BY trades DESC",
    )


# ── Lifecycle ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global agent
    required = ["AZURE_PROJECT_ENDPOINT", "MODEL_DEPLOYMENT_NAME"]
    missing  = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    print(f"[{SERVICE_NAME}] Initializing TradingPlatformAgent ...")
    agent = await create_trading_platform_agent()

    print(f"[{SERVICE_NAME}] Connecting to database ...")
    await db_ops.initialize()

    print(f"[{SERVICE_NAME}] Ready — docs at http://localhost:8989/docs")


@app.on_event("shutdown")
async def shutdown():
    await db_ops.close()
    print(f"[{SERVICE_NAME}] Shutdown complete.")


# ── Helpers ────────────────────────────────────────────────────────────────────
def _get_thread(session_id: str) -> AgentThread:
    """Return the existing AgentThread for a session, or create a new one."""
    if session_id not in sessions:
        sessions[session_id] = AgentThread()
    return sessions[session_id]


def _require_agent():
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized yet.")


def _require_db():
    if db_ops.pool is None:
        raise HTTPException(status_code=503, detail="Database not connected yet.")


# ── Root & health ──────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "docs":    "/docs",
        "endpoints": {
            "health":               "GET  /health",
            "chat":                 "POST /chat",
            "clear_session":        "POST /clear_session",
            "accounts":             "GET  /accounts",
            "all_summaries":        "GET  /portfolio/summary/all",
            "portfolio":            "GET  /portfolio/{account_id}",
            "trades":               "GET  /portfolio/{account_id}/trades",
            "events_account":       "GET  /portfolio/{account_id}/events",
            "events_account_ticker":"GET  /portfolio/{account_id}/{ticker_symbol}/events",
            "latest_price":         "GET  /ticker/{ticker_symbol}/price",
            "events_ticker":        "GET  /ticker/{ticker_symbol}/events",
            "insert_event":         "POST /events",
            "query":                "POST /query",
            "agent_status":         "GET  /agent/status",
        },
    }


@app.get("/health")
async def health():
    """Service health including database connectivity."""
    db_healthy = await db_ops.health_check() if db_ops.pool else False
    return {
        "status":    "healthy" if agent and db_healthy else "degraded",
        "service":   SERVICE_NAME,
        "version":   SERVICE_VERSION,
        "agent":     "ready" if agent else "initializing",
        "database":  "connected" if db_healthy else "disconnected",
        "framework": "Microsoft Agent Framework",
    }


# ── Chat ───────────────────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Natural-language chat with the Trading Platform Agent.
    Maintains conversational context per session_id.

    Example:
        { "session_id": "user-1", "message": "Show me the portfolio for account A100" }
    """
    _require_agent()
    try:
        thread   = _get_thread(request.session_id)
        response = await agent.run(request.message, thread=thread)
        return ChatResponse(
            session_id=request.session_id,
            response=response.text or "",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear_session")
async def clear_session(request: ClearSessionRequest):
    """Clear the conversation history for a session."""
    sessions.pop(request.session_id, None)
    return {"status": "cleared", "session_id": request.session_id}


# ── Accounts endpoint ─────────────────────────────────────────────────────────
@app.get("/accounts")
async def list_accounts():
    """Return a sorted list of all distinct account IDs in the ledger."""
    _require_db()
    try:
        accounts = await db_ops.get_all_accounts()
        return {"count": len(accounts), "accounts": accounts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Structured portfolio endpoints ─────────────────────────────────────────────
@app.get("/portfolio/summary/all", operation_id="getAllPortfolioSummaries")
async def all_portfolio_summaries():
    """
    Net positions for ALL accounts in a single query.
    Each row includes: account_id, ticker_symbol, net_shares, net_cost,
    last_price (chronologically latest, not max), last_price_ts, last_event_ts.
    Ideal for cross-account risk scanning — a single call replaces 25 individual
    portfolioSummary calls.
    """
    _require_db()
    try:
        rows = await db_ops.get_all_portfolio_summaries()
        return {"count": len(rows), "positions": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio/{account_id}")
async def portfolio_summary(account_id: str):
    """
    Net share position, net cost basis, and last observed price per ticker
    for the given account.
    """
    _require_db()
    try:
        rows = await db_ops.get_portfolio_summary(account_id)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for account '{account_id}'.")
        return {"account_id": account_id, "positions": rows}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio/{account_id}/trades")
async def trade_history(
    account_id: str,
    event_type: Optional[str] = Query(None, description="Filter: BUY or SELL"),
    start:      Optional[str] = Query(None, description="Start timestamp filter (ISO 8601)"),
    end:        Optional[str] = Query(None, description="End timestamp filter (ISO 8601)"),
):
    """BUY / SELL trade history for an account, optionally filtered by type and/or date range."""
    _require_db()
    try:
        rows = await db_ops.get_trade_history(
            account_id,
            event_type.upper() if event_type else None,
            start or None,
            end   or None,
        )
        return {"account_id": account_id, "event_type": event_type, "trades": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio/{account_id}/events")
async def account_events(
    account_id: str,
    start: Optional[str] = Query(None, description="Start timestamp filter (ISO 8601)"),
    end:   Optional[str] = Query(None, description="End timestamp filter (ISO 8601)"),
):
    """All ledger events (BUY, SELL, PRICE) for an account, newest first.
    Optionally filter by date range."""
    _require_db()
    try:
        rows = await db_ops.get_events_by_account(account_id, start or None, end or None)
        return {"account_id": account_id, "count": len(rows), "events": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio/{account_id}/{ticker_symbol}/events", operation_id="getAccountTickerEvents")
async def account_ticker_events(
    account_id:    str,
    ticker_symbol: str,
    start: Optional[str] = Query(None, description="Start timestamp filter (ISO 8601)"),
    end:   Optional[str] = Query(None, description="End timestamp filter (ISO 8601)"),
):
    """All ledger events for a specific account + ticker combination, newest first.
    More targeted than /portfolio/{account_id}/events when drilling into one position."""
    _require_db()
    try:
        rows = await db_ops.get_events_by_account_and_ticker(
            account_id, ticker_symbol.upper(), start or None, end or None
        )
        return {
            "account_id":    account_id,
            "ticker_symbol": ticker_symbol.upper(),
            "count":         len(rows),
            "events":        rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Ticker endpoints ───────────────────────────────────────────────────────────
@app.get("/ticker/{ticker_symbol}/price")
async def latest_price(ticker_symbol: str):
    """Most recently observed market price for a ticker."""
    _require_db()
    try:
        row = await db_ops.get_latest_price(ticker_symbol.upper())
        if not row:
            raise HTTPException(status_code=404, detail=f"No price data for '{ticker_symbol}'.")
        return row
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ticker/{ticker_symbol}/events")
async def ticker_events(
    ticker_symbol: str,
    start: Optional[str] = Query(None, description="Start timestamp filter (ISO 8601)"),
    end:   Optional[str] = Query(None, description="End timestamp filter (ISO 8601)"),
):
    """All ledger events for a ticker across all accounts, newest first.
    Optionally filter by date range."""
    _require_db()
    try:
        rows = await db_ops.get_events_by_ticker(ticker_symbol.upper(), start or None, end or None)
        return {"ticker_symbol": ticker_symbol.upper(), "count": len(rows), "events": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Analysis context (pre-computed for o1 analyst agent) ─────────────────────
@app.get("/portfolio/{account_id}/analysis-context", operation_id="getAccountAnalysisContext")
async def account_analysis_context(account_id: str):
    """
    Return a fully pre-computed analysis context for one account including
    avg_cost_per_share, unrealized_pnl, portfolio_weight per position, and
    a pre-flagged anomalies list (OVERSELL, MISSING_PRICE, STALE_PRICE).
    Designed as the input payload for the InvestmentPortfolioAnalysisAgent.
    """
    from datetime import datetime, timezone
    _require_db()
    try:
        result = await db_ops.get_account_analysis_context(account_id)
        if not result.get("holdings"):
            raise HTTPException(status_code=404, detail=f"No holdings found for account '{account_id}'.")
        # Compute anomalies (same logic as the tool wrapper)
        STALE_DAYS = 30
        now = datetime.now(timezone.utc)
        anomalies = []
        for h in result["holdings"]:
            ticker = h["ticker_symbol"]
            if (h["net_shares"] or 0) < 0:
                anomalies.append({"flag": "OVERSELL", "ticker": ticker,
                                   "details": f"net_shares={h['net_shares']} — short or data error"})
            if h["last_price"] is None:
                anomalies.append({"flag": "MISSING_PRICE", "ticker": ticker,
                                   "details": "No PRICE events found for this ticker"})
            elif h.get("last_price_ts"):
                try:
                    ts_str = str(h["last_price_ts"]).replace("Z", "+00:00")
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age = (now - ts).days
                    if age >= STALE_DAYS:
                        anomalies.append({"flag": "STALE_PRICE", "ticker": ticker,
                                           "details": f"Last price is {age} days old (>{STALE_DAYS}d threshold)"})
                except ValueError:
                    pass
        result["anomalies"] = anomalies
        result["as_of_date"] = now.isoformat()
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Event insertion ────────────────────────────────────────────────────────────
@app.post("/events", status_code=201)
async def insert_event(event: InsertEventRequest):
    """
    Insert a new portfolio event (BUY, SELL, or PRICE observation) into the ledger.
    Returns the auto-generated event id.
    """
    _require_db()
    try:
        new_id = await db_ops.insert_event(
            account_id=event.account_id,
            ticker_symbol=event.ticker_symbol,
            event_ts=event.event_ts,
            event_type=event.event_type.upper(),
            shares=event.shares,
            price_per_share=event.price_per_share,
            currency=event.currency,
            source=event.source,
        )
        return {"status": "created", "id": new_id, **event.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Dynamic read-only SQL query ─────────────────────────────────────────────────
@app.post("/query", operation_id="runQuery")
async def dynamic_query(request: QueryRequest):
    """
    Execute a read-only SELECT statement against the portfolio_event_ledger table.
    Only SELECT statements are accepted. DDL, DML, and system-table access are blocked.

    Example body:
        { "sql": "SELECT account_id, COUNT(*) AS trades FROM portfolio_event_ledger WHERE event_type IN ('BUY','SELL') GROUP BY account_id ORDER BY trades DESC" }
    """
    _require_db()
    try:
        rows = await db_ops.execute_read_query(request.sql)
        return {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else [], "rows": rows}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Agent status ───────────────────────────────────────────────────────────────
@app.get("/agent/status")
async def agent_status():
    """Trading Platform Agent capabilities and registered tools."""
    return {
        "agent":       "TradingPlatformAgent",
        "status":      "ready" if agent else "initializing",
        "description": (
            "AI agent for portfolio event ledger queries and trade operations."
        ),
        "tools": [
            {"name": "list_all_accounts",           "description": "List all distinct account IDs"},
            {"name": "get_all_portfolio_summaries",  "description": "Net positions for ALL accounts (one query, use for risk scans)"},
            {"name": "get_portfolio_summary",         "description": "Net position + cost basis per ticker for one account"},
            {"name": "get_events_by_account",        "description": "All events for an account, with optional date range"},
            {"name": "get_events_by_account_ticker", "description": "All events for a specific account + ticker"},
            {"name": "get_events_by_ticker",         "description": "All events for a ticker across all accounts"},
            {"name": "get_latest_price",             "description": "Most recent PRICE observation for a ticker"},
            {"name": "get_trade_history",            "description": "BUY/SELL history, filterable by type and date range"},
            {"name": "get_account_analysis_context", "description": "Pre-computed analysis context bundle for the o1 analyst agent"},
            {"name": "run_query",                    "description": "Execute a custom read-only SELECT against the ledger"},
            {"name": "insert_trade_event",           "description": "Insert a new ledger event"},
            {"name": "check_database_health",        "description": "DB connectivity probe"},
        ],
        "structured_endpoints": {
            "accounts":             "GET  /accounts",
            "all_summaries":        "GET  /portfolio/summary/all",
            "portfolio_summary":    "GET  /portfolio/{account_id}",
            "trade_history":        "GET  /portfolio/{account_id}/trades",
            "account_events":       "GET  /portfolio/{account_id}/events",
            "account_ticker_events":  "GET  /portfolio/{account_id}/{ticker_symbol}/events",
            "analysis_context":        "GET  /portfolio/{account_id}/analysis-context",
            "latest_price":            "GET  /ticker/{ticker_symbol}/price",
            "ticker_events":        "GET  /ticker/{ticker_symbol}/events",
            "insert_event":         "POST /events",
            "query":                "POST /query",
        },
    }
