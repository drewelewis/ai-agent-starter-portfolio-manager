-- ============================================================
-- Portfolio Event Ledger  –  DDL  (PostgreSQL 17)
-- ============================================================

-- Table
CREATE TABLE IF NOT EXISTS portfolio_event_ledger (
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

-- ============================================================
-- Indexes
-- ============================================================

-- Primary time-series query pattern: account + time range
CREATE INDEX IF NOT EXISTS idx_pel_account_ts
    ON portfolio_event_ledger (account_id, event_ts DESC);

-- Ticker-level market queries (PRICE events, position history)
CREATE INDEX IF NOT EXISTS idx_pel_ticker_ts
    ON portfolio_event_ledger (ticker_symbol, event_ts DESC);

-- Filter by event type (e.g., all BUY / SELL for P&L)
CREATE INDEX IF NOT EXISTS idx_pel_event_type
    ON portfolio_event_ledger (event_type);

-- Composite: account + ticker + time (position roll-up)
CREATE INDEX IF NOT EXISTS idx_pel_account_ticker_ts
    ON portfolio_event_ledger (account_id, ticker_symbol, event_ts DESC);

-- ============================================================
-- Sample INSERT
-- ============================================================

INSERT INTO portfolio_event_ledger
    (account_id, ticker_symbol, event_ts, event_type, shares, price_per_share, currency, source)
VALUES
    ('ACC-001', 'MSFT', '2026-02-20T14:30:00Z', 'BUY',   10.000000, 415.250000, 'USD', 'broker'),
    ('ACC-001', 'MSFT', '2026-02-20T15:00:00Z', 'PRICE',  0.000000, 416.100000, 'USD', 'market-feed'),
    ('ACC-001', 'MSFT', '2026-02-20T16:00:00Z', 'SELL',   5.000000, 417.500000, 'USD', 'broker');
