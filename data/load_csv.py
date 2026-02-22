"""
Load portfolio_event_ledger_500.csv into PostgreSQL.

Usage:
    python data/load_csv.py

Requires a .env file (or environment variables) with Azure PostgreSQL credentials:
    PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD, PG_SSL_MODE

Example .env values:
    POSTGRES_HOST=your-server.postgres.database.azure.com
    POSTGRES_PORT=5432
    POSTGRES_DB=postgres
    POSTGRES_USER=your-admin-user
    POSTGRES_PASSWORD=your-password
    POSTGRES_SSL_MODE=require
"""

import os
import csv
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Connection settings ────────────────────────────────────────────────────────
PG_HOST     = os.getenv("POSTGRES_HOST")
PG_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB       = os.getenv("POSTGRES_DB",       "postgres")
PG_USER     = os.getenv("POSTGRES_USER")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD")
PG_SSL_MODE = os.getenv("POSTGRES_SSL_MODE", "require")        # Azure requires SSL

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent
DDL_FILE = DATA_DIR / "ddl.sql"
CSV_FILE = DATA_DIR / "portfolio_event_ledger_500.csv"

INSERT_SQL = """
INSERT INTO portfolio_event_ledger
    (account_id, ticker_symbol, event_ts, event_type, shares, price_per_share, currency, source)
VALUES
    (%(account_id)s, %(ticker_symbol)s, %(event_ts)s, %(event_type)s,
     %(shares)s, %(price_per_share)s, %(currency)s, %(source)s)
"""


def main():
    if not all([PG_HOST, PG_USER, PG_PASSWORD]):
        raise ValueError("POSTGRES_HOST, POSTGRES_USER, and POSTGRES_PASSWORD must be set in .env or environment.")

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT,
        dbname=PG_DB, user=PG_USER, password=PG_PASSWORD,
        sslmode=PG_SSL_MODE
    )
    conn.autocommit = False

    with conn:
        with conn.cursor() as cur:
            # 1. Apply DDL (idempotent – uses IF NOT EXISTS)
            print(f"Applying DDL from {DDL_FILE} …")
            cur.execute(DDL_FILE.read_text())

            # 2. Load CSV rows
            print(f"Loading {CSV_FILE} …")
            with CSV_FILE.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            cur.executemany(INSERT_SQL, rows)
            print(f"Inserted {len(rows)} rows.")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
