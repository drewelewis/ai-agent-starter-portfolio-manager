"""
Trading Platform FastAPI
REST API that wraps the TradingPlatformAgent for portfolio event ledger operations.

Endpoints:
    GET  /health                          - Service health + DB connectivity
    GET  /                                - API info and endpoint map
    POST /chat                            - Natural-language chat with the agent
    POST /clear_session                   - Clear conversation history for a session
    GET  /portfolio/{account_id}          - Portfolio summary for an account
    GET  /portfolio/{account_id}/trades   - Trade history (BUY/SELL) for an account
    GET  /portfolio/{account_id}/events   - All events for an account
    GET  /ticker/{ticker_symbol}/price    - Latest observed price for a ticker
    GET  /ticker/{ticker_symbol}/events   - All events for a ticker
    POST /events                          - Insert a new portfolio event
    GET  /agent/status                    - Agent tools and capabilities
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
            "health":          "GET  /health",
            "chat":            "POST /chat",
            "clear_session":   "POST /clear_session",
            "portfolio":       "GET  /portfolio/{account_id}",
            "trades":          "GET  /portfolio/{account_id}/trades",
            "events_account":  "GET  /portfolio/{account_id}/events",
            "latest_price":    "GET  /ticker/{ticker_symbol}/price",
            "events_ticker":   "GET  /ticker/{ticker_symbol}/events",
            "insert_event":    "POST /events",
            "agent_status":    "GET  /agent/status",
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


# ── Structured portfolio endpoints ─────────────────────────────────────────────
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
    limit:      int            = Query(100,  description="Max rows to return"),
):
    """BUY / SELL trade history for an account, optionally filtered by type."""
    _require_db()
    try:
        rows = await db_ops.get_trade_history(
            account_id,
            event_type.upper() if event_type else None,
            limit,
        )
        return {"account_id": account_id, "event_type": event_type, "trades": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/portfolio/{account_id}/events")
async def account_events(
    account_id: str,
    limit: int = Query(100, description="Max rows to return"),
):
    """All ledger events (BUY, SELL, PRICE) for an account, newest first."""
    _require_db()
    try:
        rows = await db_ops.get_events_by_account(account_id, limit)
        return {"account_id": account_id, "count": len(rows), "events": rows}
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
    limit: int = Query(100, description="Max rows to return"),
):
    """All ledger events for a ticker across all accounts, newest first."""
    _require_db()
    try:
        rows = await db_ops.get_events_by_ticker(ticker_symbol.upper(), limit)
        return {"ticker_symbol": ticker_symbol.upper(), "count": len(rows), "events": rows}
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
            {"name": "get_events_by_account",         "description": "All events for an account"},
            {"name": "get_events_by_ticker",           "description": "All events for a ticker"},
            {"name": "get_portfolio_summary",          "description": "Net position + cost basis per ticker"},
            {"name": "get_latest_price",               "description": "Most recent PRICE observation"},
            {"name": "get_trade_history",              "description": "BUY/SELL history, filterable by type"},
            {"name": "insert_trade_event",             "description": "Insert a new ledger event"},
            {"name": "check_database_health",          "description": "DB connectivity probe"},
        ],
        "structured_endpoints": {
            "portfolio_summary": "GET /portfolio/{account_id}",
            "trade_history":     "GET /portfolio/{account_id}/trades",
            "account_events":    "GET /portfolio/{account_id}/events",
            "latest_price":      "GET /ticker/{ticker_symbol}/price",
            "ticker_events":     "GET /ticker/{ticker_symbol}/events",
            "insert_event":      "POST /events",
        },
    }
