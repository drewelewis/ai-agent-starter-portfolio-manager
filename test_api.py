"""
Trading Platform MCP Server Test
Tests all MCP tools exposed via Azure APIM against the portfolio event ledger.

Set in .env:
    APIM_MCP_SERVER_URL=https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp
"""

import asyncio
import json
import os
import time

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv()

MCP_URL = os.getenv(
    "APIM_MCP_SERVER_URL",
    "https://ai-learning-apim.azure-api.net/trading-platform-mcp-server/mcp",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt(data) -> str:
    text = json.dumps(data, indent=2) if not isinstance(data, str) else data
    return text[:400] + ("..." if len(text) > 400 else "")


def _parse(response) -> dict | list | str | None:
    for block in response.content:
        if hasattr(block, "text"):
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return block.text
    return None


# ---------------------------------------------------------------------------
# individual tests
# ---------------------------------------------------------------------------

async def test_health(s: ClientSession):
    print("\n[health] Checking service health...")
    data = _parse(await s.call_tool("health", arguments={}))
    ok = isinstance(data, dict) and data.get("status") == "healthy"
    print(f"  {'✅' if ok else '❌'} {_fmt(data)}")
    return ok


async def test_agent_status(s: ClientSession):
    print("\n[agentStatus] Fetching registered tools...")
    data = _parse(await s.call_tool("agentStatus", arguments={}))
    ok = isinstance(data, dict) and "tools" in data
    if ok:
        tools = data["tools"]
        names = ", ".join(t["name"] for t in tools[:5])
        print(f"  ✅ {len(tools)} tool(s): {names}")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_portfolio_summary(s: ClientSession, account_id: str = "A100"):
    print(f"\n[portfolioSummary] Account {account_id}...")
    data = _parse(await s.call_tool("portfolioSummary", arguments={"account_id": account_id}))
    # API returns {"account_id": ..., "positions": [...]}
    positions = (
        data.get("positions") if isinstance(data, dict)
        else data if isinstance(data, list)
        else None
    )
    ok = bool(positions)
    if ok:
        print(f"  ✅ {len(positions)} ticker(s)")
        for row in positions[:3]:
            if isinstance(row, dict):
                print(f"     {list(row.items())[:4]}")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_latest_price(s: ClientSession, ticker: str = "MSFT"):
    print(f"\n[latestPrice] Ticker {ticker}...")
    data = _parse(await s.call_tool("latestPrice", arguments={"ticker_symbol": ticker}))
    ok = isinstance(data, dict) and "error" not in str(data).lower()
    print(f"  {'✅' if ok else '❌'} {_fmt(data)}")
    return ok


async def test_trade_history(s: ClientSession, account_id: str = "A100"):
    print(f"\n[tradeHistory] Account {account_id}...")
    data = _parse(await s.call_tool("tradeHistory", arguments={"account_id": account_id}))
    # API returns {"account_id": ..., "event_type": ..., "trades": [...]}
    trades = (
        data.get("trades") if isinstance(data, dict)
        else data if isinstance(data, list)
        else None
    )
    ok = trades is not None
    if ok:
        print(f"  ✅ {len(trades)} trade(s)")
        for t in trades[:2]:
            if isinstance(t, dict):
                print(
                    f"     [{t.get('event_type')}] {t.get('ticker_symbol')} "
                    f"x{t.get('shares')} @ {t.get('price_per_share')}"
                )
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_account_events(s: ClientSession, account_id: str = "A100"):
    print(f"\n[accountEvents] Account {account_id}...")
    data = _parse(await s.call_tool("accountEvents", arguments={"account_id": account_id}))
    # API returns {"account_id": ..., "count": ..., "events": [...]}
    events = (
        data.get("events") if isinstance(data, dict)
        else data if isinstance(data, list)
        else None
    )
    ok = events is not None
    if ok:
        types: dict = {}
        for e in events:
            t = e.get("event_type", "?") if isinstance(e, dict) else "?"
            types[t] = types.get(t, 0) + 1
        print(f"  ✅ {len(events)} event(s): {types}")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_ticker_events(s: ClientSession, ticker: str = "MSFT"):
    print(f"\n[tickerEvents] Ticker {ticker}...")
    data = _parse(await s.call_tool("tickerEvents", arguments={"ticker_symbol": ticker}))
    # API returns {"ticker_symbol": ..., "count": ..., "events": [...]}
    events = (
        data.get("events") if isinstance(data, dict)
        else data if isinstance(data, list)
        else None
    )
    ok = events is not None
    if ok:
        accounts = {e.get("account_id") for e in events if isinstance(e, dict)}
        print(f"  ✅ {len(events)} event(s) across {len(accounts)} account(s)")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_list_accounts(s: ClientSession):
    print("\n[listAccounts] Listing all distinct accounts...")

    # APIM MCP derives tool names from operation IDs and may use various
    # naming conventions — discover the correct name at runtime.
    tools_resp = await s.list_tools()
    advertised = {t.name for t in tools_resp.tools}

    # Candidate names in priority order
    candidates = ["listAccounts", "list_accounts_accounts_get", "accounts"]
    tool_name = next((c for c in candidates if c in advertised), None)

    if tool_name is None:
        print(f"  ❌ No accounts-listing tool found in MCP. Advertised tools: {sorted(advertised)}")
        return False

    print(f"  Tool name resolved: {tool_name}")
    data = _parse(await s.call_tool(tool_name, arguments={}))
    # API returns {"count": int, "accounts": [str, ...]}
    accounts = (
        data.get("accounts") if isinstance(data, dict)
        else data if isinstance(data, list)
        else None
    )
    ok = isinstance(accounts, list) and len(accounts) > 0
    if ok:
        print(f"  ✅ {len(accounts)} account(s): {', '.join(accounts)}")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


async def test_chat(s: ClientSession):
    print("\n[chat] Asking agent about account A100...")
    # Note: APIM MCP maps POST body fields as tool arguments.
    # The chat endpoint expects {"session_id": str, "message": str}.
    data = _parse(
        await s.call_tool(
            "chat",
            arguments={
                "session_id": "mcp-test-session",
                "message": "Give me a brief portfolio summary for account A100",
            },
        )
    )
    if isinstance(data, dict) and "detail" in data:
        # FastAPI validation error — likely APIM MCP body mapping issue
        print(f"  ❌ Validation error (APIM MCP body mapping): {_fmt(data)}")
        return False
    ok = isinstance(data, dict) and bool(data.get("response"))
    if ok:
        reply = data["response"]
        print(f"  ✅ Agent replied ({len(reply)} chars): {reply[:200]}...")
    else:
        print(f"  ❌ {_fmt(data)}")
    return ok


# ---------------------------------------------------------------------------
# test registry
# ---------------------------------------------------------------------------

TESTS = [
    ("health",           test_health),
    ("agentStatus",      test_agent_status),
    ("listAccounts",     test_list_accounts),
    ("portfolioSummary", test_portfolio_summary),
    ("latestPrice",      test_latest_price),
    ("tradeHistory",     test_trade_history),
    ("accountEvents",    test_account_events),
    ("tickerEvents",     test_ticker_events),
    ("chat",             test_chat),
]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("  Trading Platform MCP Server Test")
    print(f"  URL: {MCP_URL}")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=120.0) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("  Connected — MCP session initialized")

                # list all tools the server advertises
                tools_resp = await session.list_tools()
                print(f"\n{'-' * 60}")
                print(f"MCP tools advertised ({len(tools_resp.tools)}):")
                for t in tools_resp.tools:
                    first_line = (t.description or "").splitlines()[0][:70]
                    print(f"  - {t.name}: {first_line}")
                print("-" * 60)

                # run each test
                results: dict[str, tuple[bool, float]] = {}
                for name, fn in TESTS:
                    t0 = time.time()
                    try:
                        ok = await asyncio.wait_for(fn(session), timeout=60.0)
                    except asyncio.TimeoutError:
                        print("  TIMEOUT after 60 s")
                        ok = False
                    except Exception as exc:
                        import traceback
                        print(f"  ERROR: {type(exc).__name__}: {exc}")
                        traceback.print_exc()
                        ok = False
                    results[name] = (ok, time.time() - t0)

    # summary
    print(f"\n{'=' * 60}")
    print("  Results")
    print("=" * 60)
    passed = sum(1 for ok, _ in results.values() if ok)
    for name, (ok, elapsed) in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {name:<22} {elapsed:5.1f}s")
    print("-" * 60)
    print(f"  {passed}/{len(results)} passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
