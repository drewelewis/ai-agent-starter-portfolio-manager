"""
Trading Platform Tools for Microsoft Agent Framework
Provides typed async tool functions for portfolio event ledger operations.
"""

import json
from typing import Annotated

from operations.trading_platform_operations import TradingPlatformOperations

# Shared operations instance — initialized lazily on first use
_ops = TradingPlatformOperations()


async def _get_ops() -> TradingPlatformOperations:
    """Return an initialized TradingPlatformOperations instance."""
    if _ops.pool is None:
        await _ops.initialize()
    return _ops


async def get_events_by_account(
    account_id: Annotated[str, "The account ID to retrieve events for (e.g. A100)"],
    start_ts:   Annotated[str, "Optional ISO 8601 start timestamp filter (inclusive)"] = "",
    end_ts:     Annotated[str, "Optional ISO 8601 end timestamp filter (inclusive)"] = "",
) -> str:
    """
    Get all portfolio events (BUY, SELL, PRICE) for a given account.
    Returns events ordered newest first. Optionally filter by date range.

    Args:
        account_id: The account ID to retrieve events for
        start_ts: Optional start of date range (ISO 8601)
        end_ts:   Optional end of date range (ISO 8601)

    Returns:
        A formatted string listing the portfolio events
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_events_by_account(
            account_id,
            start_ts or None,
            end_ts   or None,
        )
        if not rows:
            return f"No events found for account '{account_id}'."
        lines = [f"Found {len(rows)} event(s) for account '{account_id}':"]
        for r in rows:
            lines.append(
                f"  [{r['event_ts']}] {r['event_type']:5s}  {r['ticker_symbol']:6s}  "
                f"shares={r['shares']}  price={r['price_per_share']}  {r['currency']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving events for account '{account_id}': {e}"


async def get_events_by_ticker(
    ticker_symbol: Annotated[str, "The ticker symbol to retrieve events for (e.g. MSFT)"],
    start_ts:      Annotated[str, "Optional ISO 8601 start timestamp filter (inclusive)"] = "",
    end_ts:        Annotated[str, "Optional ISO 8601 end timestamp filter (inclusive)"] = "",
) -> str:
    """
    Get all portfolio events for a given ticker symbol across all accounts.
    Returns events ordered newest first. Optionally filter by date range.

    Args:
        ticker_symbol: The ticker symbol (e.g. MSFT, AAPL)
        start_ts: Optional start of date range (ISO 8601)
        end_ts:   Optional end of date range (ISO 8601)

    Returns:
        A formatted string listing the events for that ticker
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_events_by_ticker(
            ticker_symbol,
            start_ts or None,
            end_ts   or None,
        )
        if not rows:
            return f"No events found for ticker '{ticker_symbol}'."
        lines = [f"Found {len(rows)} event(s) for ticker '{ticker_symbol}':"]
        for r in rows:
            lines.append(
                f"  [{r['event_ts']}] {r['event_type']:5s}  account={r['account_id']}  "
                f"shares={r['shares']}  price={r['price_per_share']}  {r['currency']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving events for ticker '{ticker_symbol}': {e}"


async def get_portfolio_summary(
    account_id: Annotated[str, "The account ID to summarise (e.g. A100)"],
) -> str:
    """
    Get a portfolio summary for an account showing net share position,
    net cost basis, and latest observed price for each ticker held.

    Args:
        account_id: The account ID to summarise

    Returns:
        A formatted summary of the account's portfolio positions
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_portfolio_summary(account_id)
        if not rows:
            return f"No portfolio data found for account '{account_id}'."
        lines = [f"Portfolio summary for account '{account_id}':"]
        lines.append(f"  {'Ticker':<8} {'Net Shares':>12} {'Net Cost':>14} {'Last Price':>12}  {'Price Date':<24} {'Last Event'}")
        lines.append("  " + "-" * 86)
        for r in rows:
            last_price_ts = str(r.get('last_price_ts') or 'NO PRICE EVENTS')
            lines.append(
                f"  {r['ticker_symbol']:<8} {float(r['net_shares'] or 0):>12.4f} "
                f"{float(r['net_cost'] or 0):>14.2f} "
                f"{float(r['last_price'] or 0):>12.2f}  "
                f"{last_price_ts:<24}  {r['last_event_ts']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving portfolio summary for account '{account_id}': {e}"


async def get_latest_price(
    ticker_symbol: Annotated[str, "The ticker symbol to get the latest price for (e.g. MSFT)"],
) -> str:
    """
    Get the most recently observed market price for a ticker symbol.

    Args:
        ticker_symbol: The ticker symbol (e.g. MSFT, AAPL)

    Returns:
        The latest price and timestamp for the ticker
    """
    try:
        ops = await _get_ops()
        row = await ops.get_latest_price(ticker_symbol)
        if not row:
            return f"No price data found for ticker '{ticker_symbol}'."
        return (
            f"Latest price for {row['ticker_symbol']}: "
            f"{row['price_per_share']} {row['currency']} "
            f"(as of {row['event_ts']})"
        )
    except Exception as e:
        return f"Error retrieving latest price for ticker '{ticker_symbol}': {e}"


async def get_trade_history(
    account_id: Annotated[str, "The account ID to retrieve trade history for"],
    event_type: Annotated[str, "Filter by event type: BUY, SELL, or leave empty for both"] = "",
    start_ts:   Annotated[str, "Optional ISO 8601 start timestamp filter (inclusive)"] = "",
    end_ts:     Annotated[str, "Optional ISO 8601 end timestamp filter (inclusive)"] = "",
) -> str:
    """
    Get BUY and/or SELL trade history for an account, optionally filtered
    by event type and/or date range.

    Args:
        account_id: The account ID to retrieve trade history for
        event_type: Optional filter — 'BUY', 'SELL', or empty for both
        start_ts:   Optional start of date range (ISO 8601)
        end_ts:     Optional end of date range (ISO 8601)

    Returns:
        A formatted string listing the trade history
    """
    try:
        ops = await _get_ops()
        filter_type = event_type.upper() if event_type else None
        rows = await ops.get_trade_history(
            account_id,
            filter_type,
            start_ts or None,
            end_ts   or None,
        )
        if not rows:
            label = f"'{filter_type}' trades" if filter_type else "trades"
            return f"No {label} found for account '{account_id}'."
        label = f"'{filter_type}' trade(s)" if filter_type else "trade(s)"
        lines = [f"Found {len(rows)} {label} for account '{account_id}':"]
        for r in rows:
            lines.append(
                f"  [{r['event_ts']}] {r['event_type']:4s}  {r['ticker_symbol']:6s}  "
                f"shares={r['shares']}  price={r['price_per_share']}  {r['currency']}  source={r['source']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving trade history for account '{account_id}': {e}"


async def insert_trade_event(
    account_id: Annotated[str, "The account ID (e.g. A100)"],
    ticker_symbol: Annotated[str, "The ticker symbol (e.g. MSFT)"],
    event_ts: Annotated[str, "Event timestamp in ISO 8601 UTC format (e.g. 2026-02-20T14:30:00Z)"],
    event_type: Annotated[str, "Event type: BUY, SELL, or PRICE"],
    shares: Annotated[float, "Number of shares (use 0 for PRICE events)"],
    price_per_share: Annotated[float, "Executed trade price or observed market price"],
    currency: Annotated[str, "ISO currency code (e.g. USD)"],
    source: Annotated[str, "Origin of the event (e.g. broker, market-feed, synthetic)"],
) -> str:
    """
    Insert a new portfolio event (BUY, SELL, or PRICE observation) into the ledger.

    Args:
        account_id: The account ID
        ticker_symbol: The ticker symbol
        event_ts: Event timestamp in ISO 8601 UTC format
        event_type: BUY, SELL, or PRICE
        shares: Number of shares (0 for PRICE events)
        price_per_share: Trade or market price
        currency: ISO currency code
        source: Origin of the event

    Returns:
        Confirmation message with the new event ID
    """
    try:
        ops = await _get_ops()
        new_id = await ops.insert_event(
            account_id, ticker_symbol, event_ts, event_type.upper(),
            shares, price_per_share, currency, source,
        )
        return (
            f"Event recorded successfully (id={new_id}): "
            f"{event_type.upper()} {shares} shares of {ticker_symbol} "
            f"@ {price_per_share} {currency} for account '{account_id}'."
        )
    except Exception as e:
        return f"Error inserting event for account '{account_id}': {e}"


async def list_all_accounts() -> str:
    """
    List all distinct account IDs that exist in the portfolio event ledger.

    Returns:
        A formatted string with all account IDs, sorted alphabetically
    """
    try:
        ops = await _get_ops()
        accounts = await ops.get_all_accounts()
        if not accounts:
            return "No accounts found in the ledger."
        lines = [f"Found {len(accounts)} account(s):"] + [f"  {a}" for a in accounts]
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving account list: {e}"


async def get_all_portfolio_summaries() -> str:
    """
    Get portfolio positions for ALL accounts in a single query.
    Returns net shares, net cost, last price, and last-price timestamp per
    account+ticker combination. Ideal for cross-account risk scanning.
    Detects oversold positions (net_shares < 0), missing prices (last_price null),
    and stale prices (last_price_ts far in the past).

    Returns:
        A formatted table of all account positions across the entire ledger
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_all_portfolio_summaries()
        if not rows:
            return "No position data found in the ledger."
        lines = [f"All-accounts portfolio summary ({len(rows)} positions):"]
        lines.append(
            f"  {'Account':<38} {'Ticker':<8} {'Net Shares':>12} "
            f"{'Net Cost':>14} {'Last Price':>12}  {'Price Date':<24} Flags"
        )
        lines.append("  " + "-" * 120)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for r in rows:
            net = float(r["net_shares"] or 0)
            lp  = r["last_price"]
            lpts = r["last_price_ts"]
            flags = []
            if net < 0:
                flags.append("OVERSELL")
            if lp is None:
                flags.append("NO_PRICE")
            elif lpts:
                days_stale = (now - lpts.replace(tzinfo=timezone.utc) if lpts.tzinfo is None else now - lpts).days
                if days_stale > 30:
                    flags.append(f"STALE_PRICE({days_stale}d)")
            flag_str = ", ".join(flags) if flags else ""
            lines.append(
                f"  {r['account_id']:<38} {r['ticker_symbol']:<8} {net:>12.4f} "
                f"{float(r['net_cost'] or 0):>14.2f} "
                f"{float(lp or 0):>12.2f}  "
                f"{str(lpts or 'None'):<24}  {flag_str}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving all portfolio summaries: {e}"


async def get_events_by_account_ticker(
    account_id:    Annotated[str, "The account ID (e.g. A100)"],
    ticker_symbol: Annotated[str, "The ticker symbol (e.g. MSFT)"],
    start_ts:      Annotated[str, "Optional ISO 8601 start timestamp filter (inclusive)"] = "",
    end_ts:        Annotated[str, "Optional ISO 8601 end timestamp filter (inclusive)"] = "",
) -> str:
    """
    Get all ledger events (BUY, SELL, PRICE) for a specific account AND ticker.
    More targeted than accountEvents — use when drilling into a single position.
    Optionally filter by date range.

    Args:
        account_id:    The account ID
        ticker_symbol: The ticker symbol
        start_ts:      Optional start of date range (ISO 8601)
        end_ts:        Optional end of date range (ISO 8601)

    Returns:
        A formatted string of events for that account+ticker combination
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_events_by_account_and_ticker(
            account_id,
            ticker_symbol.upper(),
            start_ts or None,
            end_ts   or None,
        )
        if not rows:
            return f"No events found for account '{account_id}', ticker '{ticker_symbol}'."
        lines = [f"Found {len(rows)} event(s) for {ticker_symbol.upper()} in account '{account_id}':"]
        for r in rows:
            lines.append(
                f"  [{r['event_ts']}] {r['event_type']:5s}  "
                f"shares={r['shares']}  price={r['price_per_share']}  {r['currency']}  source={r['source']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving events for account '{account_id}', ticker '{ticker_symbol}': {e}"


async def run_query(
    sql: Annotated[str, "A read-only SELECT SQL statement against portfolio_event_ledger"],
) -> str:
    """
    Execute a custom read-only SELECT query against the portfolio_event_ledger table.
    Use this when no other tool can answer the question — e.g. for aggregations,
    cross-account comparisons, or custom filters not supported by specific tools.

    The table schema:
        portfolio_event_ledger(
            id, account_id, ticker_symbol, event_ts TIMESTAMPTZ,
            event_type (BUY|SELL|PRICE), shares NUMERIC,
            price_per_share NUMERIC, currency, source, created_at
        )

    Only SELECT statements are permitted. DDL, DML, and system-table access
    are blocked server-side.

    Args:
        sql: A valid PostgreSQL SELECT statement

    Returns:
        Query results as a formatted table or JSON, with row count
    """
    try:
        ops = await _get_ops()
        rows = await ops.execute_read_query(sql)
        if not rows:
            return "Query returned 0 rows."
        headers = list(rows[0].keys())
        lines = [f"Query returned {len(rows)} row(s):", "  " + "  ".join(f"{h}" for h in headers)]
        lines.append("  " + "-" * 80)
        for r in rows:
            lines.append("  " + "  ".join(str(r[h]) for h in headers))
        return "\n".join(lines)
    except ValueError as e:
        return f"Query rejected: {e}"
    except Exception as e:
        return f"Query error: {e}"


async def get_account_analysis_context(account_id: str) -> str:
    """
    Return a fully pre-computed analysis context bundle for one account,
    ready for the InvestmentPortfolioAnalysisAgent (o1 model).

    Computes per-position: avg_cost_per_share, unrealized_pnl, portfolio_weight.
    Also includes top-level summary (total_market_value, positions counts)
    and a pre-flagged anomalies list (OVERSELL, MISSING_PRICE, STALE_PRICE).

    Args:
        account_id: UUID of the account to analyse.
    Returns:
        JSON string matching the InvestmentPortfolioAnalysisAgent input schema.
    """
    import json
    from datetime import datetime, timezone, timedelta

    try:
        ops = await _get_ops()
        result = await ops.get_account_analysis_context(account_id)
    except Exception as e:
        return f"Error fetching analysis context: {e}"

    if not result.get("holdings"):
        return json.dumps({"account_id": account_id, "error": "No holdings found"})

    # Detect anomalies
    STALE_DAYS = 30
    now = datetime.now(timezone.utc)
    anomalies = []
    for h in result["holdings"]:
        ticker = h["ticker_symbol"]
        if h["net_shares"] < 0:
            anomalies.append({
                "flag":    "OVERSELL",
                "ticker":  ticker,
                "details": f"net_shares={h['net_shares']:.4f} — short position or data error",
            })
        if h["last_price"] is None:
            anomalies.append({
                "flag":    "MISSING_PRICE",
                "ticker":  ticker,
                "details": "No PRICE events found for this ticker",
            })
        elif h["last_price_ts"]:
            try:
                ts = datetime.fromisoformat(h["last_price_ts"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_days = (now - ts).days
                if age_days >= STALE_DAYS:
                    anomalies.append({
                        "flag":    "STALE_PRICE",
                        "ticker":  ticker,
                        "details": f"Last price is {age_days} days old (>{STALE_DAYS}d threshold)",
                    })
            except ValueError:
                pass

    output = {
        "as_of_date": now.isoformat(),
        "account_id": account_id,
        "summary":    result["summary"],
        "holdings":   result["holdings"],
        "anomalies":  anomalies,
    }
    return json.dumps(output, indent=2, default=str)


async def check_database_health() -> str:
    """
    Check whether the trading platform database connection is healthy.

    Returns:
        A status message indicating if the database is reachable
    """
    try:
        ops = await _get_ops()
        healthy = await ops.health_check()
        return "Database connection is healthy." if healthy else "Database connection is NOT healthy."
    except Exception as e:
        return f"Database health check failed: {e}"
