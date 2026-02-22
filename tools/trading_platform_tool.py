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
    limit: Annotated[int, "Maximum number of events to return (default 100)"] = 100,
) -> str:
    """
    Get all portfolio events (BUY, SELL, PRICE) for a given account.
    Returns events ordered newest first.

    Args:
        account_id: The account ID to retrieve events for
        limit: Maximum number of events to return

    Returns:
        A formatted string listing the portfolio events
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_events_by_account(account_id, limit)
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
    limit: Annotated[int, "Maximum number of events to return (default 100)"] = 100,
) -> str:
    """
    Get all portfolio events for a given ticker symbol across all accounts.
    Returns events ordered newest first.

    Args:
        ticker_symbol: The ticker symbol (e.g. MSFT, AAPL)
        limit: Maximum number of events to return

    Returns:
        A formatted string listing the events for that ticker
    """
    try:
        ops = await _get_ops()
        rows = await ops.get_events_by_ticker(ticker_symbol, limit)
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
        lines.append(f"  {'Ticker':<8} {'Net Shares':>12} {'Net Cost':>14} {'Last Price':>12} {'Last Event'}")
        lines.append("  " + "-" * 64)
        for r in rows:
            lines.append(
                f"  {r['ticker_symbol']:<8} {float(r['net_shares'] or 0):>12.4f} "
                f"{float(r['net_cost'] or 0):>14.2f} "
                f"{float(r['last_price'] or 0):>12.2f} "
                f"  {r['last_event_ts']}"
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
    limit: Annotated[int, "Maximum number of trades to return (default 100)"] = 100,
) -> str:
    """
    Get BUY and/or SELL trade history for an account, optionally filtered
    to a specific event type.

    Args:
        account_id: The account ID to retrieve trade history for
        event_type: Optional filter — 'BUY', 'SELL', or empty for both
        limit: Maximum number of trades to return

    Returns:
        A formatted string listing the trade history
    """
    try:
        ops = await _get_ops()
        filter_type = event_type.upper() if event_type else None
        rows = await ops.get_trade_history(account_id, filter_type, limit)
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
