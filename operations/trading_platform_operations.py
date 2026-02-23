"""
Trading Platform Operations
Handles all database interactions for the portfolio event ledger.
"""

import asyncio
import os
from typing import Any, Dict, List, Optional
from functools import wraps

import asyncpg
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Connection settings from .env ──────────────────────────────────────────────
POSTGRES_HOST     = os.getenv("POSTGRES_HOST")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB       = os.getenv("POSTGRES_DB", "postgres")
POSTGRES_USER     = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_SSL      = os.getenv("POSTGRES_SSL_MODE", "require")


def retry_on_db_error(max_retries: int = 3, delay: float = 0.5):
    """Decorator to retry database operations on transient errors."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (
                    asyncpg.ConnectionDoesNotExistError,
                    asyncpg.InterfaceError,
                    asyncpg.TooManyConnectionsError,
                ) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)
                        print(f"[TradingPlatformOperations] DB error on attempt {attempt + 1}/{max_retries}, "
                              f"retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        print(f"[TradingPlatformOperations] DB operation failed after {max_retries} attempts: {e}")
                        raise
                except Exception as e:
                    print(f"[TradingPlatformOperations] Non-retryable DB error: {e}")
                    raise
            raise last_error
        return wrapper
    return decorator


class TradingPlatformOperations:
    """Handles PostgreSQL operations for the portfolio_event_ledger table."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self.agent_name = "trading_platform_operations"

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self._pool

    async def initialize(self):
        """Initialize the shared database connection pool."""
        if self._pool is not None:
            print("[TradingPlatformOperations] Pool already initialized, skipping.")
            return

        if not all([POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD]):
            raise ValueError(
                "POSTGRES_HOST, POSTGRES_USER, and POSTGRES_PASSWORD must be set in .env."
            )

        print("[TradingPlatformOperations] Connecting to database:")
        print(f"  host={POSTGRES_HOST}")
        print(f"  port={POSTGRES_PORT}")
        print(f"  user={POSTGRES_USER}")
        print(f"  database={POSTGRES_DB}")
        print(f"  ssl={POSTGRES_SSL}")

        self._pool = await asyncpg.create_pool(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB,
            min_size=2,
            max_size=10,
            max_queries=50000,
            max_inactive_connection_lifetime=3600,
            command_timeout=60,
            timeout=30,
            ssl=POSTGRES_SSL,
            init=self._init_connection,
        )

        if self._pool is None:
            raise RuntimeError("Failed to create database connection pool.")

        # Validate connectivity
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                if result != 1:
                    raise RuntimeError("Database connection test failed.")
        except Exception as e:
            await self._pool.close()
            self._pool = None
            raise RuntimeError(f"Database connection validation failed: {e}")

        print("[TradingPlatformOperations] Connection pool initialized and verified.")

    async def _init_connection(self, conn):
        """Per-connection initialization."""
        await conn.execute("SET statement_timeout = 60000")
        await conn.execute(f"SET application_name = 'portfolio_manager_{self.agent_name}'")

    async def close(self):
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def health_check(self) -> bool:
        """Return True if the database is reachable."""
        try:
            if not self._pool:
                return False
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            print(f"[TradingPlatformOperations] Health check failed: {e}")
            return False

    # ── Generic helpers ────────────────────────────────────────────────────────

    @retry_on_db_error(max_retries=3)
    async def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return results as a list of dicts.
        Converts %s placeholders to $1, $2, … for asyncpg."""
        if params and "%s" in query:
            for i in range(1, len(params) + 1):
                query = query.replace("%s", f"${i}", 1)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params) if params else await conn.fetch(query)
            return [dict(row) for row in rows] if rows else []

    _FORBIDDEN_SQL = frozenset([
        "insert", "update", "delete", "drop", "truncate", "alter", "create",
        "replace", "upsert", "merge", "grant", "revoke", "copy", "vacuum",
        "pg_", "information_schema",
    ])

    async def execute_read_query(
        self, sql: str, params: tuple = None
    ) -> List[Dict[str, Any]]:
        """Execute a caller-supplied SELECT query with safety guardrails.
        Rejects any statement that is not a SELECT or that contains DDL/DML keywords.
        Results are always returned as a list of plain dicts.
        """
        normalized = sql.lower().strip()
        if not normalized.startswith("select"):
            raise ValueError("Only SELECT statements are permitted via execute_read_query.")
        for kw in self._FORBIDDEN_SQL:
            if kw in normalized:
                raise ValueError(f"Forbidden keyword '{kw}' in query.")
        return await self.execute_query(sql, params)

    @retry_on_db_error(max_retries=3)
    async def execute_update(self, query: str, params: tuple = None) -> None:
        """Execute an INSERT, UPDATE, or DELETE query.
        Converts %s placeholders to $1, $2, … for asyncpg."""
        if params and "%s" in query:
            for i in range(1, len(params) + 1):
                query = query.replace("%s", f"${i}", 1)

        async with self._pool.acquire() as conn:
            await conn.execute(query, *params) if params else await conn.execute(query)

    # ── Domain methods ─────────────────────────────────────────────────────────

    @retry_on_db_error(max_retries=3)
    async def insert_event(
        self,
        account_id: str,
        ticker_symbol: str,
        event_ts: str,
        event_type: str,
        shares: float,
        price_per_share: float,
        currency: str,
        source: str,
    ) -> int:
        """Insert a single portfolio event and return the new row id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO portfolio_event_ledger
                    (account_id, ticker_symbol, event_ts, event_type,
                     shares, price_per_share, currency, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                account_id, ticker_symbol, event_ts, event_type,
                shares, price_per_share, currency, source,
            )
            return row["id"]

    @retry_on_db_error(max_retries=3)
    async def get_events_by_account(
        self,
        account_id: str,
        start_ts: Optional[str] = None,
        end_ts:   Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all ledger events for a given account, newest first.
        Optionally filter to a date range via ISO 8601 start_ts / end_ts."""
        async with self._pool.acquire() as conn:
            params: list = [account_id]
            clauses = ["account_id = $1"]
            if start_ts:
                params.append(start_ts)
                clauses.append(f"event_ts >= ${len(params)}")
            if end_ts:
                params.append(end_ts)
                clauses.append(f"event_ts <= ${len(params)}")
            where = " AND ".join(clauses)
            rows = await conn.fetch(
                f"SELECT * FROM portfolio_event_ledger WHERE {where} ORDER BY event_ts DESC",
                *params,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_events_by_ticker(
        self,
        ticker_symbol: str,
        start_ts: Optional[str] = None,
        end_ts:   Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all ledger events for a given ticker across all accounts, newest first.
        Optionally filter to a date range via ISO 8601 start_ts / end_ts."""
        async with self._pool.acquire() as conn:
            params: list = [ticker_symbol]
            clauses = ["ticker_symbol = $1"]
            if start_ts:
                params.append(start_ts)
                clauses.append(f"event_ts >= ${len(params)}")
            if end_ts:
                params.append(end_ts)
                clauses.append(f"event_ts <= ${len(params)}")
            where = " AND ".join(clauses)
            rows = await conn.fetch(
                f"SELECT * FROM portfolio_event_ledger WHERE {where} ORDER BY event_ts DESC",
                *params,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_events_by_account_and_ticker(
        self,
        account_id:    str,
        ticker_symbol: str,
        start_ts: Optional[str] = None,
        end_ts:   Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return ledger events for a specific account + ticker, newest first.
        Optionally filter to a date range via ISO 8601 start_ts / end_ts."""
        async with self._pool.acquire() as conn:
            params: list = [account_id, ticker_symbol]
            clauses = ["account_id = $1", "ticker_symbol = $2"]
            if start_ts:
                params.append(start_ts)
                clauses.append(f"event_ts >= ${len(params)}")
            if end_ts:
                params.append(end_ts)
                clauses.append(f"event_ts <= ${len(params)}")
            where = " AND ".join(clauses)
            rows = await conn.fetch(
                f"SELECT * FROM portfolio_event_ledger WHERE {where} ORDER BY event_ts DESC",
                *params,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_portfolio_summary(self, account_id: str) -> List[Dict[str, Any]]:
        """Return net position, cost basis, and most-recent PRICE per ticker for an account.
        last_price is sourced from the chronologically latest PRICE event (not MAX price).
        Adds last_price_ts so callers can detect stale price data."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked_prices AS (
                    SELECT ticker_symbol, price_per_share, event_ts,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker_symbol
                               ORDER BY event_ts DESC
                           ) AS rn
                    FROM portfolio_event_ledger
                    WHERE account_id = $1 AND event_type = 'PRICE'
                )
                SELECT
                    t.account_id,
                    t.ticker_symbol,
                    SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares
                             WHEN t.event_type = 'SELL' THEN -t.shares
                             ELSE 0 END)                               AS net_shares,
                    SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares * t.price_per_share
                             WHEN t.event_type = 'SELL' THEN -t.shares * t.price_per_share
                             ELSE 0 END)                               AS net_cost,
                    rp.price_per_share                                 AS last_price,
                    rp.event_ts                                        AS last_price_ts,
                    MAX(t.event_ts)                                    AS last_event_ts
                FROM portfolio_event_ledger t
                LEFT JOIN ranked_prices rp
                       ON rp.ticker_symbol = t.ticker_symbol AND rp.rn = 1
                WHERE t.account_id = $1
                GROUP BY t.account_id, t.ticker_symbol, rp.price_per_share, rp.event_ts
                ORDER BY t.ticker_symbol
                """,
                account_id,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_all_portfolio_summaries(self) -> List[Dict[str, Any]]:
        """Return net positions across ALL accounts in a single query.
        Identical logic to get_portfolio_summary but no account_id filter.
        Efficient for full-portfolio risk scans."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked_prices AS (
                    SELECT account_id, ticker_symbol, price_per_share, event_ts,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id, ticker_symbol
                               ORDER BY event_ts DESC
                           ) AS rn
                    FROM portfolio_event_ledger
                    WHERE event_type = 'PRICE'
                )
                SELECT
                    t.account_id,
                    t.ticker_symbol,
                    SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares
                             WHEN t.event_type = 'SELL' THEN -t.shares
                             ELSE 0 END)                               AS net_shares,
                    SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares * t.price_per_share
                             WHEN t.event_type = 'SELL' THEN -t.shares * t.price_per_share
                             ELSE 0 END)                               AS net_cost,
                    rp.price_per_share                                 AS last_price,
                    rp.event_ts                                        AS last_price_ts,
                    MAX(t.event_ts)                                    AS last_event_ts
                FROM portfolio_event_ledger t
                LEFT JOIN ranked_prices rp
                       ON rp.account_id = t.account_id
                      AND rp.ticker_symbol = t.ticker_symbol
                      AND rp.rn = 1
                WHERE t.event_type IN ('BUY', 'SELL')
                GROUP BY t.account_id, t.ticker_symbol, rp.price_per_share, rp.event_ts
                ORDER BY t.account_id, t.ticker_symbol
                """
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_account_analysis_context(
        self, account_id: str
    ) -> Dict[str, Any]:
        """Return a fully pre-computed analysis context for one account,
        ready for the InvestmentPortfolioAnalysisAgent (o1 model).
        Derives: avg_cost_per_share, unrealized_pnl, portfolio_weight,
        and summary metadata including anomaly flags."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked_prices AS (
                    SELECT ticker_symbol, price_per_share, event_ts,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker_symbol
                               ORDER BY event_ts DESC
                           ) AS rn
                    FROM portfolio_event_ledger
                    WHERE account_id = $1 AND event_type = 'PRICE'
                ),
                holdings AS (
                    SELECT
                        t.account_id,
                        t.ticker_symbol,
                        SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares ELSE 0 END)
                            AS total_buy_shares,
                        SUM(CASE WHEN t.event_type = 'SELL' THEN t.shares ELSE 0 END)
                            AS total_sell_shares,
                        SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares
                                 WHEN t.event_type = 'SELL' THEN -t.shares
                                 ELSE 0 END)
                            AS net_shares,
                        SUM(CASE WHEN t.event_type = 'BUY'  THEN t.shares * t.price_per_share
                                 WHEN t.event_type = 'SELL' THEN -t.shares * t.price_per_share
                                 ELSE 0 END)
                            AS net_cost,
                        rp.price_per_share  AS last_price,
                        rp.event_ts         AS last_price_ts,
                        MAX(t.event_ts)     AS last_event_ts
                    FROM portfolio_event_ledger t
                    LEFT JOIN ranked_prices rp
                           ON rp.ticker_symbol = t.ticker_symbol AND rp.rn = 1
                    WHERE t.account_id = $1
                    GROUP BY t.account_id, t.ticker_symbol, rp.price_per_share, rp.event_ts
                ),
                portfolio_totals AS (
                    SELECT
                        SUM(CASE WHEN last_price IS NOT NULL AND net_shares > 0
                                 THEN net_shares * last_price ELSE 0 END)
                            AS total_market_value,
                        COUNT(*)
                            AS total_positions,
                        SUM(CASE WHEN last_price IS NOT NULL THEN 1 ELSE 0 END)
                            AS positions_with_price,
                        SUM(CASE WHEN last_price IS NULL THEN 1 ELSE 0 END)
                            AS positions_no_price
                    FROM holdings
                )
                SELECT
                    h.account_id,
                    h.ticker_symbol,
                    h.net_shares,
                    h.net_cost,
                    h.last_price,
                    h.last_price_ts,
                    h.last_event_ts,
                    h.total_buy_shares,
                    h.total_sell_shares,
                    CASE WHEN h.total_buy_shares > 0
                         THEN h.net_cost / h.total_buy_shares
                         ELSE NULL END                                         AS avg_cost_per_share,
                    CASE WHEN h.last_price IS NOT NULL
                         THEN h.net_shares * h.last_price - h.net_cost
                         ELSE NULL END                                         AS unrealized_pnl,
                    CASE WHEN h.last_price IS NOT NULL AND pt.total_market_value > 0
                         THEN (h.net_shares * h.last_price) / pt.total_market_value
                         ELSE NULL END                                         AS portfolio_weight,
                    pt.total_market_value,
                    pt.total_positions,
                    pt.positions_with_price,
                    pt.positions_no_price
                FROM holdings h
                CROSS JOIN portfolio_totals pt
                ORDER BY
                    CASE WHEN h.last_price IS NOT NULL THEN h.net_shares * h.last_price ELSE 0 END DESC
                """,
                account_id,
            )
            if not rows:
                return {"account_id": account_id, "holdings": [], "summary": None}

            # Extract summary metadata from first row (same for all rows)
            first = dict(rows[0])
            summary = {
                "account_id":           account_id,
                "total_market_value":   float(first["total_market_value"] or 0),
                "total_positions":      int(first["total_positions"]),
                "positions_with_price": int(first["positions_with_price"]),
                "positions_no_price":   int(first["positions_no_price"]),
            }

            holdings = []
            for row in rows:
                r = dict(row)
                holdings.append({
                    "account_id":          r["account_id"],
                    "ticker_symbol":       r["ticker_symbol"],
                    "net_shares":          float(r["net_shares"] or 0),
                    "net_cost":            float(r["net_cost"] or 0),
                    "last_price":          float(r["last_price"]) if r["last_price"] is not None else None,
                    "last_price_ts":       str(r["last_price_ts"]) if r["last_price_ts"] else None,
                    "last_event_ts":       str(r["last_event_ts"]) if r["last_event_ts"] else None,
                    "avg_cost_per_share":  float(r["avg_cost_per_share"]) if r["avg_cost_per_share"] is not None else None,
                    "unrealized_pnl":      float(r["unrealized_pnl"]) if r["unrealized_pnl"] is not None else None,
                    "portfolio_weight":    float(r["portfolio_weight"]) if r["portfolio_weight"] is not None else None,
                })

            return {"account_id": account_id, "summary": summary, "holdings": holdings}

    @retry_on_db_error(max_retries=3)
    async def get_latest_price(
        self, ticker_symbol: str
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent PRICE event for a ticker."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT ticker_symbol, price_per_share, currency, event_ts
                FROM portfolio_event_ledger
                WHERE ticker_symbol = $1 AND event_type = 'PRICE'
                ORDER BY event_ts DESC
                LIMIT 1
                """,
                ticker_symbol,
            )
            return dict(row) if row else None

    @retry_on_db_error(max_retries=3)
    async def get_trade_history(
        self,
        account_id: str,
        event_type: Optional[str] = None,
        start_ts:   Optional[str] = None,
        end_ts:     Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return BUY/SELL trade history for an account.
        Optionally filter by event_type and/or a date range."""
        async with self._pool.acquire() as conn:
            params: list = [account_id]
            if event_type:
                params.append(event_type)
                type_clause = f"event_type = ${len(params)}"
            else:
                type_clause = "event_type IN ('BUY', 'SELL')"
            clauses = ["account_id = $1", type_clause]
            if start_ts:
                params.append(start_ts)
                clauses.append(f"event_ts >= ${len(params)}")
            if end_ts:
                params.append(end_ts)
                clauses.append(f"event_ts <= ${len(params)}")
            where = " AND ".join(clauses)
            rows = await conn.fetch(
                f"SELECT * FROM portfolio_event_ledger WHERE {where} ORDER BY event_ts DESC",
                *params,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_all_accounts(self) -> List[str]:
        """Return a sorted list of all distinct account IDs in the ledger."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT account_id
                FROM portfolio_event_ledger
                ORDER BY account_id
                """
            )
            return [row["account_id"] for row in rows]
