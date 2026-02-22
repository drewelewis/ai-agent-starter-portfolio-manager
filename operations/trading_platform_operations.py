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
        self, account_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return all ledger events for a given account, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM portfolio_event_ledger
                WHERE account_id = $1
                ORDER BY event_ts DESC
                LIMIT $2
                """,
                account_id, limit,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_events_by_ticker(
        self, ticker_symbol: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return all ledger events for a given ticker, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM portfolio_event_ledger
                WHERE ticker_symbol = $1
                ORDER BY event_ts DESC
                LIMIT $2
                """,
                ticker_symbol, limit,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_events_by_account_and_ticker(
        self, account_id: str, ticker_symbol: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return ledger events for a specific account + ticker, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM portfolio_event_ledger
                WHERE account_id = $1 AND ticker_symbol = $2
                ORDER BY event_ts DESC
                LIMIT $3
                """,
                account_id, ticker_symbol, limit,
            )
            return [dict(row) for row in rows]

    @retry_on_db_error(max_retries=3)
    async def get_portfolio_summary(self, account_id: str) -> List[Dict[str, Any]]:
        """Return net share position and average cost per ticker for an account.
        Considers BUY events as positive and SELL events as negative shares."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    account_id,
                    ticker_symbol,
                    SUM(CASE WHEN event_type = 'BUY'  THEN shares
                             WHEN event_type = 'SELL' THEN -shares
                             ELSE 0 END)                          AS net_shares,
                    SUM(CASE WHEN event_type = 'BUY'  THEN shares * price_per_share
                             WHEN event_type = 'SELL' THEN -shares * price_per_share
                             ELSE 0 END)                          AS net_cost,
                    MAX(CASE WHEN event_type = 'PRICE' THEN price_per_share END) AS last_price,
                    MAX(event_ts)                                 AS last_event_ts
                FROM portfolio_event_ledger
                WHERE account_id = $1
                GROUP BY account_id, ticker_symbol
                ORDER BY ticker_symbol
                """,
                account_id,
            )
            return [dict(row) for row in rows]

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
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return BUY/SELL trade history for an account, optionally filtered by event_type."""
        async with self._pool.acquire() as conn:
            if event_type:
                rows = await conn.fetch(
                    """
                    SELECT * FROM portfolio_event_ledger
                    WHERE account_id = $1 AND event_type = $2
                    ORDER BY event_ts DESC
                    LIMIT $3
                    """,
                    account_id, event_type, limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM portfolio_event_ledger
                    WHERE account_id = $1 AND event_type IN ('BUY', 'SELL')
                    ORDER BY event_ts DESC
                    LIMIT $2
                    """,
                    account_id, limit,
                )
            return [dict(row) for row in rows]

            g.close()

    def search_code(self, query: str) -> List[str]:
        try:
            g = Github(pat, per_page=100)
            results = g.search_code(query=query)
            code_snippets = [result.code for result in results]
            return code_snippets
        except Exception as e:
            print(f"An error occurred with GitHubOperations.search_code: {e}")
            return []
        finally:
            g.close()

    def create_issue(self, repo: str, title: str, body: str) -> str:
        try:
            g = Github(pat, per_page=100)
            repository = g.get_repo(repo)
            if not repository:
                raise ValueError(f"Repository '{repo}' not found.")
            
            issue = repository.create_issue(title=title, body=body)
            return f"Issue created successfully: {issue.html_url}"
        except Exception as e:
            print(f"An error occurred with GitHubOperations.create_issue: {e}")
            return ""
        finally:
            g.close()
    
