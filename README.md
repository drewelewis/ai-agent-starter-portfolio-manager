# Trading Platform AI Agent

An AI-powered portfolio event ledger API built with **Microsoft Agent Framework**, **FastAPI**, and **Azure PostgreSQL** — deployed to **Azure Container Apps** and exposed as an **MCP server** via **Azure API Management**.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Azure Infrastructure](#azure-infrastructure)
5. [Environment Variables](#environment-variables)
6. [API Endpoints](#api-endpoints)
7. [Database Schema](#database-schema)
8. [APIM MCP Gateway](#apim-mcp-gateway)
9. [Local Development](#local-development)
10. [Docker](#docker)
11. [Helper Scripts](#helper-scripts)
12. [Testing](#testing)

---

## Overview

The Trading Platform AI Agent provides two surfaces for interacting with a portfolio event ledger:

| Surface | Protocol | URL |
|---------|----------|-----|
| REST API | HTTP/JSON | `https://<aca-host>/` |
| MCP Server (AI Gateway) | MCP / Streamable HTTP | `https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp` |

The REST API exposes structured query endpoints and a natural-language **chat** endpoint backed by an Azure OpenAI-powered agent. The MCP server (hosted in Azure API Management) makes all 11 endpoints discoverable and callable by any MCP-compatible client — Claude Desktop, VS Code Copilot, custom agent frameworks — without a subscription key.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  MCP Clients                                             │
│  (Claude Desktop · VS Code Copilot · Custom Agents)      │
└────────────────┬─────────────────────────────────────────┘
                 │  MCP protocol (Streamable HTTP / SSE)
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Azure API Management  (BasicV2+)                        │
│  ai-learning-apim.azure-api.net                          │
│                                                          │
│  ┌─────────────────────────────────────────────────┐     │
│  │ MCP Server: trading-platform-mcp-server         │     │
│  │ path: /trading-platform-mcp-server              │     │
│  │ type: mcp   subscriptionRequired: false         │     │
│  │ 11 tools: health, agentStatus, portfolioSummary │     │
│  │   latestPrice, tradeHistory, accountEvents,     │     │
│  │   tickerEvents, chat, clearSession, insertEvent │     │
│  │   root                                          │     │
│  └──────────────────────┬──────────────────────────┘     │
│                         │ REST proxy                      │
│  ┌────────────────────┐ └──────────────────────────────┐  │
│  │ REST API           │                               │  │
│  │ trading-platform-api                               │  │
│  │ path: /  (root)                                   │  │
│  │ subscriptionRequired: false                       │  │
│  └───────────────────────────────────────────────────┘  │
└────────────────┬─────────────────────────────────────────┘
                 │  HTTPS
                 ▼
┌──────────────────────────────────────────────────────────┐
│  Azure Container Apps                                    │
│  ai-learning-aca  (East US)                              │
│  min-replicas: 0  |  ingress: external  |  port: 8989   │
│                                                          │
│  Trading Platform FastAPI (Python 3.11)                  │
│  ├── GET  /health                                        │
│  ├── GET  /                                              │
│  ├── POST /chat                      ─┐                  │
│  ├── POST /clear_session              ├─ Agent endpoints │
│  ├── GET  /portfolio/{account_id}    ─┘                  │
│  ├── GET  /portfolio/{account_id}/trades                 │
│  ├── GET  /portfolio/{account_id}/events                 │
│  ├── GET  /ticker/{ticker_symbol}/price                  │
│  ├── GET  /ticker/{ticker_symbol}/events                 │
│  ├── POST /events                                        │
│  └── GET  /agent/status                                  │
└──────┬───────────────────┬────────────────────────────────┘
       │                   │
       │ asyncpg           │ azure-identity (DefaultAzureCredential)
       ▼                   ▼
┌─────────────┐   ┌────────────────────────────────────────┐
│  Azure      │   │  Azure OpenAI / AI Foundry             │
│  PostgreSQL │   │  (direct model endpoint)               │
│  Flexible   │   │  Model: gpt-4.1 (or configured)        │
│  Server     │   │  Auth: Managed Identity                │
│  (SSL)      │   └────────────────────────────────────────┘
└─────────────┘
```

### Request Flow — MCP Tool Call (e.g. `portfolioSummary`)

```
Client --[MCP call_tool "portfolioSummary" {account_id: "A100"}]--> APIM
APIM   --[GET /portfolio/A100]--> Container Apps
Container Apps --[asyncpg SELECT]--> PostgreSQL
PostgreSQL --[rows]--> Container Apps
Container Apps --[JSON {account_id, positions: [...]}]--> APIM
APIM   --[MCP tool result]--> Client
```

### Request Flow — NL Chat via MCP

```
Client --[MCP call_tool "chat" {session_id, message}]--> APIM
              ↓ inbound policy reconstructs JSON body
APIM   --[POST /chat {session_id, message}]--> Container Apps
Container Apps --[agent.run(message, thread)]--> Azure OpenAI
Azure OpenAI --[tool_calls: get_portfolio_summary, ...]-->
  Container Apps executes tools against PostgreSQL
  Container Apps --[final text response]--> APIM
APIM --[MCP tool result {response: "..."}]--> Client
```

---

## Project Structure

```
.
├── main.py                          # Uvicorn entry point (port 8989)
├── chat.py                          # Interactive CLI chat client
├── requirements.txt                 # Python dependencies
├── dockerfile                       # Production container image (python:3.11-slim)
├── docker-compose.yaml              # Local stack: API + PostgreSQL + Adminer
├── env.sample                       # Template — copy to .env
│
├── api/
│   ├── main.py                      # FastAPI app — all REST endpoints
│   └── main_with_proxy.py           # Alternative with APIM proxy headers
│
├── agents/
│   └── trading_platform_agent.py   # ChatAgent definition + system prompt
│
├── operations/
│   └── trading_platform_operations.py  # asyncpg queries (connection pool, retry)
│
├── tools/
│   └── trading_platform_tool.py    # ai_function wrappers (agent-callable tools)
│
├── models/
│   └── chat_models.py              # Pydantic request/response models
│
├── data/
│   ├── ddl.sql                      # PostgreSQL schema + indexes
│   ├── portfolio_event_ledger_500.csv  # Synthetic seed data (500 rows)
│   └── portfolio_event_ledger_schema.json
│
├── tests/
│   └── test_mcp.py → (root)        # (see test_mcp.py below)
│
├── test_mcp.py                      # End-to-end MCP server test (8 tools)
│
└── infra/
    └── apim-mcp-body.json          # APIM MCP ARM body reference
```

---

## Azure Infrastructure

### Required Resources

| Resource | SKU / Tier | Notes |
|----------|-----------|-------|
| **Resource Group** | — | `ai-learning-rg` |
| **Azure Container Registry** | Basic+ | Stores `drewl/ai-agent-starter-portfolio-manager` |
| **Azure Container Apps Environment** | Consumption | `ai-learning-aca`, East US |
| **Azure Container Apps** (app) | — | min-replicas: 0, port 8989, external ingress |
| **Azure PostgreSQL Flexible Server** | Burstable B1ms+ | SSL required, `portfolio_event_ledger` table |
| **Azure OpenAI** or **AI Foundry** | gpt-4.1 (or GPT-4o) | Direct model endpoint |
| **Azure API Management** | **BasicV2+** | BasicV2 minimum — required for MCP server feature |

> **Important:** APIM MCP server support (`type: "mcp"`) requires **BasicV2 tier or higher**. The Developer and Consumption tiers do not support this feature.

### APIM Configuration

Two APIs are configured in APIM:

| APIM Object | Type | Path | Auth |
|-------------|------|------|------|
| `trading-platform-api` | REST | `/` (root) | Anonymous |
| `trading-platform-mcp-server` | MCP | `/trading-platform-mcp-server` | Anonymous |

The MCP API is created as `type: mcp` via the Azure Portal (API Management → APIs → + Add API → MCP Server). It auto-discovers the 11 tools from the OpenAPI spec of the REST backend.

### Managed Identity

The Container App uses a **system-assigned managed identity** with the following role assignments:

| Role | Scope | Purpose |
|------|-------|---------|
| `Cognitive Services OpenAI User` | Azure OpenAI resource | Call the model endpoint |

Authentication uses `DefaultAzureCredential` — managed identity in Azure, `az login` locally.

---

## Environment Variables

Copy `env.sample` to `.env` and fill in:

```env
# ── Azure OpenAI (direct model endpoint) ──────────────────
AZURE_OPENAI_API_ENDPOINT=https://your-resource.openai.azure.com/
MODEL_DEPLOYMENT_NAME=gpt-4.1

# ── Azure AI Foundry (alternative — if using Foundry project endpoint) ──
AZURE_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project

# ── Azure PostgreSQL Flexible Server ──────────────────────
POSTGRES_HOST=your-server.postgres.database.azure.com
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=your-admin-user
POSTGRES_PASSWORD=your-password
POSTGRES_SSL_MODE=require

# ── API Server ─────────────────────────────────────────────
SERVER_HOST=0.0.0.0
SERVER_PORT=8989
SERVER_RELOAD=false          # true = uvicorn --reload (dev only)
SERVICE_NAME=trading-platform-api
SERVICE_VERSION=1.0.0
SERVER_URL=                  # Public base URL for Swagger UI (e.g. https://your-aca-host)

# ── Docker ─────────────────────────────────────────────────
DOCKER_REPO_NAME=drewl/ai-agent-starter-portfolio-manager

# ── APIM MCP ───────────────────────────────────────────────
APIM_MCP_SERVER_URL=https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp

# ── Azure Credentials (optional — for local dev without az login) ──
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_TENANT_ID=
```

---

## API Endpoints

Base URL (production): `https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io`  
Interactive docs: `<base-url>/docs`

### GET `/`
Service info and endpoint map.

**Response:**
```json
{
  "service": "trading-platform-api",
  "version": "1.0.0",
  "docs": "/docs",
  "endpoints": { ... }
}
```

---

### GET `/health`
Service health check including database connectivity.

**Response:**
```json
{
  "status": "healthy",
  "service": "trading-platform-api",
  "version": "1.0.0",
  "agent": "ready",
  "database": "connected",
  "framework": "Microsoft Agent Framework"
}
```

`status` is `"healthy"` only when both the agent and database are ready. Degrades to `"degraded"` otherwise.

---

### POST `/chat`
Natural-language chat with the Trading Platform Agent. Maintains full conversational context per `session_id`.

**Request:**
```json
{ "session_id": "user-1", "message": "Give me a portfolio summary for account A100" }
```

**Response:**
```json
{
  "session_id": "user-1",
  "response": "Account A100 holds 175 shares of MSFT ...",
  "agent": "TradingPlatformAgent"
}
```

The agent has access to 7 tools and will call them automatically to answer the question. Conversation history is maintained in-memory per `session_id`.

---

### POST `/clear_session`
Clears the conversation history for a session.

**Request:**
```json
{ "session_id": "user-1" }
```

**Response:**
```json
{ "status": "cleared", "session_id": "user-1" }
```

---

### GET `/portfolio/{account_id}`
Net share position, net cost basis, and last observed price per ticker for an account.

**Example:** `GET /portfolio/A100`

**Response:**
```json
{
  "account_id": "A100",
  "positions": [
    {
      "account_id": "A100",
      "ticker_symbol": "MSFT",
      "net_shares": 175.0,
      "net_cost": 52830.0,
      "last_price": 416.10,
      "last_event_ts": "2026-02-20T15:00:00+00:00"
    }
  ]
}
```

Calculation: `net_shares = SUM(BUY shares) - SUM(SELL shares)`, `net_cost = SUM(BUY value) - SUM(SELL value)`.

---

### GET `/portfolio/{account_id}/trades`
BUY and SELL trade history for an account.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `event_type` | string | — | Filter by `BUY` or `SELL` |
| `limit` | int | 100 | Max rows |

**Example:** `GET /portfolio/A100/trades?event_type=BUY&limit=5`

**Response:**
```json
{
  "account_id": "A100",
  "event_type": "BUY",
  "trades": [
    {
      "id": 499,
      "account_id": "A100",
      "ticker_symbol": "MSFT",
      "event_ts": "2026-02-03T07:15:00+00:00",
      "event_type": "BUY",
      "shares": 10.0,
      "price_per_share": 349.5,
      "currency": "USD",
      "source": "synthetic"
    }
  ]
}
```

---

### GET `/portfolio/{account_id}/events`
All ledger events (BUY, SELL, PRICE) for an account, newest first.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Max rows |

**Response:**
```json
{
  "account_id": "A100",
  "count": 100,
  "events": [ { ... }, ... ]
}
```

---

### GET `/ticker/{ticker_symbol}/price`
Most recently observed market price for a ticker (latest `PRICE` event).

**Example:** `GET /ticker/MSFT/price`

**Response:**
```json
{
  "ticker_symbol": "MSFT",
  "price_per_share": 416.10,
  "currency": "USD",
  "event_ts": "2026-02-20T15:00:00+00:00"
}
```

Returns `404` if no PRICE events exist for the ticker.

---

### GET `/ticker/{ticker_symbol}/events`
All ledger events for a ticker across all accounts, newest first.

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Max rows |

**Response:**
```json
{
  "ticker_symbol": "MSFT",
  "count": 100,
  "events": [ { ... }, ... ]
}
```

---

### POST `/events`
Insert a new portfolio event into the ledger.

**Request:**
```json
{
  "account_id": "A100",
  "ticker_symbol": "MSFT",
  "event_ts": "2026-02-22T10:00:00Z",
  "event_type": "BUY",
  "shares": 5.0,
  "price_per_share": 420.0,
  "currency": "USD",
  "source": "api"
}
```

`event_type` must be `BUY`, `SELL`, or `PRICE`. Use `shares: 0` for PRICE events.

**Response (201):**
```json
{ "status": "created", "id": 501, "account_id": "A100", ... }
```

---

### GET `/agent/status`
Agent capabilities and registered tool list.

**Response:**
```json
{
  "agent": "TradingPlatformAgent",
  "status": "ready",
  "tools": [
    { "name": "get_events_by_account", "description": "All events for an account" },
    { "name": "get_events_by_ticker", "description": "All events for a ticker" },
    { "name": "get_portfolio_summary", "description": "Net position + cost basis per ticker" },
    { "name": "get_latest_price", "description": "Most recent PRICE observation" },
    { "name": "get_trade_history", "description": "BUY/SELL history, filterable by type" },
    { "name": "insert_trade_event", "description": "Insert a new ledger event" },
    { "name": "check_database_health", "description": "DB connectivity probe" }
  ]
}
```

---

## Database Schema

### Table: `portfolio_event_ledger`

```sql
CREATE TABLE portfolio_event_ledger (
    id               BIGSERIAL       PRIMARY KEY,
    account_id       VARCHAR(64)     NOT NULL,
    ticker_symbol    VARCHAR(16)     NOT NULL,
    event_ts         TIMESTAMPTZ     NOT NULL,
    event_type       VARCHAR(8)      NOT NULL CHECK (event_type IN ('BUY', 'SELL', 'PRICE')),
    shares           NUMERIC(18, 6)  NOT NULL DEFAULT 0,
    price_per_share  NUMERIC(18, 6)  NOT NULL,
    currency         VARCHAR(8)      NOT NULL,
    source           VARCHAR(128)    NOT NULL,
    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
```

### Columns

| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL | Auto-increment PK |
| `account_id` | VARCHAR(64) | e.g. `A100`, `ACC-001` |
| `ticker_symbol` | VARCHAR(16) | e.g. `MSFT`, `AAPL` |
| `event_ts` | TIMESTAMPTZ | When the event occurred |
| `event_type` | VARCHAR(8) | `BUY` / `SELL` / `PRICE` |
| `shares` | NUMERIC(18,6) | Number of shares; `0` for PRICE events |
| `price_per_share` | NUMERIC(18,6) | Trade price or market observation |
| `currency` | VARCHAR(8) | ISO code, e.g. `USD` |
| `source` | VARCHAR(128) | `broker`, `market-feed`, `api`, `synthetic` |
| `created_at` | TIMESTAMPTZ | Row insert timestamp |

### Indexes

```sql
-- Primary query pattern: account + time
CREATE INDEX idx_pel_account_ts      ON portfolio_event_ledger (account_id, event_ts DESC);
-- Ticker market queries
CREATE INDEX idx_pel_ticker_ts       ON portfolio_event_ledger (ticker_symbol, event_ts DESC);
-- Event type filtering (P&L)
CREATE INDEX idx_pel_event_type      ON portfolio_event_ledger (event_type);
-- Position roll-up
CREATE INDEX idx_pel_account_ticker  ON portfolio_event_ledger (account_id, ticker_symbol, event_ts DESC);
```

Apply schema: `psql -h <host> -U <user> -d postgres -f data/ddl.sql`  
Load seed data: `psql -h <host> -U <user> -d postgres -c "\COPY portfolio_event_ledger FROM 'data/portfolio_event_ledger_500.csv' CSV HEADER"`

---

## APIM MCP Gateway

### Endpoint

```
https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp
```

No API key required (`subscriptionRequired: false`).

### MCP Tools (11)

| Tool | Maps to | Description |
|------|---------|-------------|
| `health` | `GET /health` | Service + DB health |
| `agentStatus` | `GET /agent/status` | Registered tools |
| `portfolioSummary` | `GET /portfolio/{account_id}` | Net positions |
| `latestPrice` | `GET /ticker/{ticker_symbol}/price` | Latest market price |
| `tradeHistory` | `GET /portfolio/{account_id}/trades` | BUY/SELL history |
| `accountEvents` | `GET /portfolio/{account_id}/events` | All account events |
| `tickerEvents` | `GET /ticker/{ticker_symbol}/events` | All ticker events |
| `chat` | `POST /chat` | NL chat with agent |
| `clearSession` | `POST /clear_session` | Clear session history |
| `insertEvent` | `POST /events` | Insert new event |
| `root` | `GET /` | Service info |

### Using from Python

```python
import asyncio, httpx, os
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv()
MCP_URL = os.getenv("APIM_MCP_SERVER_URL")

async def main():
    async with httpx.AsyncClient(timeout=120.0) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                # List tools
                tools = await session.list_tools()
                # Call a tool
                result = await session.call_tool("latestPrice", {"ticker_symbol": "MSFT"})
                print(result.content[0].text)

asyncio.run(main())
```

### APIM MCP Preview — POST Body Workaround

**Issue:** APIM MCP (`2025-03-01-preview`) correctly proxies path/query parameters for GET endpoints but does **not** serialize MCP tool arguments into a JSON request body for POST endpoints. FastAPI receives an empty body and returns HTTP 422.

**Affected tools:** `chat`, `clearSession`, `insertEvent`

**Fix:** An APIM inbound policy on each POST operation reconstructs the JSON body from the query parameters APIM forwards:

```xml
<policies>
  <inbound>
    <base />
    <!-- APIM MCP preview: rebuild JSON body from query params -->
    <set-body>@{
      try {
        var body = context.Request.Body?.As&lt;JObject&gt;(true);
        if (body != null &amp;&amp; body.ContainsKey("session_id")) { return body.ToString(); }
      } catch {}
      var s = context.Request.Url.Query.GetValueOrDefault("session_id", "mcp-session");
      var m = context.Request.Url.Query.GetValueOrDefault("message", "");
      return new JObject(
        new JProperty("session_id", s),
        new JProperty("message", m)
      ).ToString();
    }</set-body>
    <set-header name="Content-Type" exists-action="override">
      <value>application/json</value>
    </set-header>
  </inbound>
  <backend><base /></backend>
  <outbound><base /></outbound>
  <on-error><base /></on-error>
</policies>
```

Deploy via:
```bash
# Build policy_body.json: { "properties": { "value": "<policies>...</policies>", "format": "xml" } }
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/{subId}/resourceGroups/ai-learning-rg/providers/Microsoft.ApiManagement/service/ai-learning-apim/apis/trading-platform-api/operations/chat_chat_post/policies/policy?api-version=2022-08-01" \
  --body "@policy_body.json" \
  --headers "Content-Type=application/json"
```

---

## Local Development

### Prerequisites

- Python 3.11+
- Azure CLI (`az login`)
- Access to Azure OpenAI or AI Foundry (`AZURE_OPENAI_API_ENDPOINT` or `AZURE_PROJECT_ENDPOINT`)
- PostgreSQL connection (Azure or local Docker)

### Setup

```bash
# 1. Create and activate virtual environment
_env_create.bat
_env_activate.bat

# 2. Install dependencies
_install.bat

# 3. Copy and fill in environment variables
copy env.sample .env
# Edit .env with your values

# 4. Start the API server locally
python main.py
# → http://localhost:8989/docs
```

### Interactive CLI Chat

```bash
python chat.py
```

Presents a REPL that sends messages to `POST /chat` and prints responses.

---

## Docker

### Build & Run

```bash
# Build image
_build.bat

# Start full stack (API + PostgreSQL + Adminer)
_up.bat

# View logs
_logs.bat

# Stop
_down.bat
```

Services started by `_up.bat`:

| Service | Port | Description |
|---------|------|-------------|
| `ai-agent-starter-api` | 8989 | Trading Platform API |
| `ai-agent-starter-portfolio-manager-postgres` | 5000→5432 | PostgreSQL 17 |
| `ai-agent-starter-portfolio-manager-adminer` | 8888 | Adminer DB UI |

### Push to Registry

```bash
_push.bat
# Pushes image to DOCKER_REPO_NAME defined in .env
```

---

## Helper Scripts

| Script | Action |
|--------|--------|
| `_env_create.bat` | `python -m venv .venv` |
| `_env_activate.bat` | activate `.venv` |
| `_env_deactivate.bat` | deactivate `.venv` |
| `_install.bat` | `pip install -r requirements.txt` |
| `_build.bat` | `docker build` |
| `_up.bat` | `docker compose up -d` |
| `_down.bat` | `docker compose down` |
| `_logs.bat` | `docker compose logs -f` |
| `_push.bat` | `docker push` to registry |

---

## Testing

### MCP End-to-End Test

Tests all 8 core MCP tools against the live APIM endpoint:

```bash
python test_mcp.py
```

**Expected output:**
```
============================================================
  Trading Platform MCP Server Test
  URL: https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp
============================================================
  Connected — MCP session initialized

MCP tools advertised (11): health, agentStatus, chat, ...

[health]           ✅  healthy
[agentStatus]      ✅  7 tools
[portfolioSummary] ✅  1 ticker(s)
[latestPrice]      ✅  MSFT @ 416.10
[tradeHistory]     ✅  67 trades
[accountEvents]    ✅  100 events
[tickerEvents]     ✅  100 events
[chat]             ✅  Agent replied (212 chars)
------------------------------------------------------------
  8/8 passed
```

Requires `APIM_MCP_SERVER_URL` in `.env`.

### REST API (manual)

```bash
# Health
curl https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io/health

# Portfolio summary
curl https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io/portfolio/A100

# Latest price
curl https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io/ticker/MSFT/price

# NL chat
curl -X POST https://ai-learning-aca.ashycliff-5cba4403.eastus.azurecontainerapps.io/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test", "message": "Summarize portfolio A100"}'
```

---

## License

This project is licensed under the terms specified in the [LICENSE](LICENSE) file.
