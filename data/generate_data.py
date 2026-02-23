"""
Generate a rich synthetic portfolio event ledger CSV for agent stress-testing.

Design goals
────────────
• 25 accounts with UUID IDs
• 60-day window: Dec 24 2025 → Feb 23 2026 (ANCHOR_DATE)
• 10-15 active holdings per account, drawn from S&P 500 tickers
• 1-3 trades/week per normal account (low churn / realistic)
• Weekly PRICE events per ticker (enables unrealized P&L calculation)

7 embedded detectable issues:
    1. Tech-concentrated account   (account idx 22 — only tech tickers)
    2. Small-cap concentrated      (account idx 23 — Russell 2000 tickers)
    3. Energy-only account         (account idx 24 — single sector)
    4. Oversell event              (account idx  4 — net_shares goes negative)
    5. Stale price data            (accounts idx 7,14 — no PRICE in last 30 days)
    6. Missing price entirely      (account idx 10 — 2 tickers, zero PRICE events)
    7. High-churn outlier          (account idx 16 — 4-5 trades/week)

Output: data/portfolio_event_ledger_500.csv  (replaces the original stub)
"""

import csv
import uuid
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
ANCHOR_DATE  = datetime(2026, 2, 23, tzinfo=timezone.utc)
START_DATE   = ANCHOR_DATE - timedelta(days=60)   # 2025-12-24
TOTAL_DAYS   = (ANCHOR_DATE - START_DATE).days     # 61
SEED         = 42
NUM_ACCOUNTS = 25

# Special account indices
TECH_HEAVY_IDX   = 22
SMALL_CAP_IDX    = 23
ENERGY_ONLY_IDX  = 24
OVERSELL_IDX     = 4
STALE_PRICE_IDXS = [7, 14]   # no PRICE after first 30 days for 2 tickers
NO_PRICE_IDX     = 10        # 2 tickers get zero PRICE events
HIGH_CHURN_IDX   = 16        # 4-5 trades/week instead of 1-3

# ── Ticker universe ─────────────────────────────────────────────────────────────
# (base_price, daily_vol_pct, sector)
SP500_TICKERS = {
    # Technology
    "MSFT":  (430.0,  0.015, "Technology"),
    "AAPL":  (245.0,  0.014, "Technology"),
    "GOOGL": (195.0,  0.016, "Technology"),
    "META":  (610.0,  0.018, "Technology"),
    "NVDA":  (135.0,  0.025, "Technology"),
    "AMD":   (130.0,  0.025, "Technology"),
    "CRM":   (340.0,  0.017, "Technology"),
    "ADBE":  (490.0,  0.016, "Technology"),
    "INTC":  (22.0,   0.020, "Technology"),
    "CSCO":  (58.0,   0.012, "Technology"),
    "TSLA":  (350.0,  0.030, "Technology"),
    "NFLX":  (995.0,  0.018, "Communication"),
    # Healthcare
    "JNJ":   (155.0,  0.009, "Healthcare"),
    "UNH":   (530.0,  0.013, "Healthcare"),
    "PFE":   (26.0,   0.012, "Healthcare"),
    "ABT":   (125.0,  0.011, "Healthcare"),
    "MRK":   (97.0,   0.011, "Healthcare"),
    "ABBV":  (175.0,  0.013, "Healthcare"),
    "TMO":   (540.0,  0.013, "Healthcare"),
    "DHR":   (230.0,  0.012, "Healthcare"),
    "BMY":   (65.0,   0.011, "Healthcare"),
    # Financials
    "JPM":   (240.0,  0.012, "Financials"),
    "BAC":   (46.0,   0.013, "Financials"),
    "WFC":   (75.0,   0.013, "Financials"),
    "GS":    (580.0,  0.014, "Financials"),
    "MS":    (130.0,  0.013, "Financials"),
    "BLK":   (1050.0, 0.013, "Financials"),
    "AXP":   (305.0,  0.013, "Financials"),
    # Consumer Discretionary
    "AMZN":  (220.0,  0.016, "Consumer Discretionary"),
    "HD":    (415.0,  0.012, "Consumer Discretionary"),
    "NKE":   (78.0,   0.014, "Consumer Discretionary"),
    # Consumer Staples
    "MCD":   (295.0,  0.009, "Consumer Staples"),
    "WMT":   (95.0,   0.010, "Consumer Staples"),
    "COST":  (975.0,  0.012, "Consumer Staples"),
    "TGT":   (130.0,  0.014, "Consumer Staples"),
    "SBUX":  (103.0,  0.013, "Consumer Staples"),
    # Energy
    "XOM":   (118.0,  0.013, "Energy"),
    "CVX":   (157.0,  0.013, "Energy"),
    "COP":   (110.0,  0.014, "Energy"),
    "SLB":   (43.0,   0.016, "Energy"),
    "EOG":   (128.0,  0.014, "Energy"),
    "MPC":   (175.0,  0.015, "Energy"),
    "HAL":   (28.0,   0.017, "Energy"),
    "VLO":   (178.0,  0.015, "Energy"),
    "PSX":   (145.0,  0.013, "Energy"),
    # Industrials
    "HON":   (214.0,  0.011, "Industrials"),
    "GE":    (185.0,  0.013, "Industrials"),
    "CAT":   (365.0,  0.013, "Industrials"),
    "LMT":   (490.0,  0.010, "Industrials"),
    "RTX":   (125.0,  0.011, "Industrials"),
    "MMM":   (140.0,  0.012, "Industrials"),
    # Utilities
    "NEE":   (73.0,   0.010, "Utilities"),
    "DUK":   (115.0,  0.009, "Utilities"),
    "SO":    (89.0,   0.009, "Utilities"),
    # Materials
    "LIN":   (475.0,  0.011, "Materials"),
    "SHW":   (380.0,  0.012, "Materials"),
    "NEM":   (45.0,   0.015, "Materials"),
    "FCX":   (47.0,   0.018, "Materials"),
    # Real Estate
    "AMT":   (200.0,  0.012, "Real Estate"),
    "PLD":   (108.0,  0.012, "Real Estate"),
    "EQIX":  (870.0,  0.013, "Real Estate"),
    # Communication
    "DIS":   (115.0,  0.013, "Communication"),
    "VZ":    (43.0,   0.009, "Communication"),
    "TMUS":  (240.0,  0.012, "Communication"),
}

# Russell 2000 small-caps (NOT S&P 500)
SMALL_CAP_TICKERS = {
    "SMCI":  (47.0,   0.040, "Technology"),
    "CROX":  (95.0,   0.035, "Consumer Discretionary"),
    "BOOT":  (105.0,  0.030, "Consumer Discretionary"),
    "XPEL":  (28.0,   0.038, "Consumer Discretionary"),
    "ATSG":  (23.0,   0.032, "Industrials"),
    "PRCT":  (82.0,   0.035, "Healthcare"),
    "PEBO":  (19.0,   0.030, "Financials"),
    "AAON":  (78.0,   0.028, "Industrials"),
    "BBIO":  (32.0,   0.045, "Healthcare"),
    "RELY":  (25.0,   0.038, "Financials"),
    "PCVX":  (68.0,   0.040, "Healthcare"),
    "CVCO":  (380.0,  0.030, "Consumer Discretionary"),
}

TECH_ONLY    = ["NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMD", "CRM", "ADBE", "INTC", "TSLA", "CSCO", "NFLX"]
ENERGY_ONLY  = ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "HAL", "VLO", "PSX"]

# Sector pools for diverse normal accounts
DIVERSE_POOL = {
    "Technology":             ["MSFT", "AAPL", "GOOGL", "CRM", "ADBE", "CSCO"],
    "Healthcare":             ["JNJ", "UNH", "ABT", "MRK", "ABBV", "TMO", "DHR", "BMY", "PFE"],
    "Financials":             ["JPM", "BAC", "WFC", "GS", "MS", "AXP"],
    "Consumer Discretionary": ["AMZN", "HD", "NKE"],
    "Consumer Staples":       ["MCD", "WMT", "COST", "TGT", "SBUX"],
    "Energy":                 ["XOM", "CVX", "COP"],
    "Industrials":            ["HON", "GE", "CAT", "LMT", "RTX"],
    "Utilities":              ["NEE", "DUK", "SO"],
    "Materials":              ["LIN", "SHW", "NEM"],
    "Real Estate":            ["AMT", "PLD", "EQIX"],
    "Communication":          ["NFLX", "DIS", "VZ", "TMUS"],
}

# ── Date helpers ───────────────────────────────────────────────────────────────
def all_trading_days():
    days, d = [], START_DATE
    while d <= ANCHOR_DATE:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days

def all_mondays():
    days = []
    d = START_DATE
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= ANCHOR_DATE:
        days.append(d)
        d += timedelta(weeks=1)
    return days

def build_weeks(t_days, mondays):
    weeks = []
    for mon in mondays:
        wk = [d for d in t_days if mon <= d < mon + timedelta(days=5)]
        if wk:
            weeks.append(wk)
    return weeks

TRADING_DAYS = all_trading_days()
MONDAYS      = all_mondays()
WEEKS        = build_weeks(TRADING_DAYS, MONDAYS)

# ── Price simulation ───────────────────────────────────────────────────────────
def simulate_prices(base, vol):
    """Random walk. Returns list of length TOTAL_DAYS+1."""
    rng = random.Random(int(base * 1000))   # deterministic per ticker
    prices = [base]
    for _ in range(TOTAL_DAYS):
        change = rng.gauss(0, vol)
        prices.append(round(max(prices[-1] * (1 + change), 0.01), 4))
    return prices

all_ticker_universe = {**SP500_TICKERS, **SMALL_CAP_TICKERS}
PRICE_SERIES = {t: simulate_prices(base, vol) for t, (base, vol, _) in all_ticker_universe.items()}

def price_at(ticker, dt):
    offset = min((dt - START_DATE).days, TOTAL_DAYS)
    return round(PRICE_SERIES[ticker][offset], 2)

def day_offset(dt):
    return (dt - START_DATE).days

# ── Output accumulator ─────────────────────────────────────────────────────────
rows = []

def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

def add_event(account_id, ticker, dt, event_type, shares, source="broker"):
    rows.append({
        "account_id":     account_id,
        "ticker_symbol":  ticker,
        "event_ts":       ts(dt),
        "event_type":     event_type,
        "shares":         str(shares),
        "price_per_share": str(price_at(ticker, dt)),
        "currency":       "USD",
        "source":         source,
    })

def add_price_events(account_id, ticker, mons, max_day_offset=None):
    for mon in mons:
        off = day_offset(mon)
        if max_day_offset is not None and off > max_day_offset:
            continue
        add_event(account_id, ticker, mon.replace(hour=16, minute=0), "PRICE", 0, "market-feed")

def add_buy(account_id, ticker, dt, shares):
    add_event(account_id, ticker, dt, "BUY", shares)

def add_sell(account_id, ticker, dt, shares):
    add_event(account_id, ticker, dt, "SELL", shares)

# ── Ticker selector ────────────────────────────────────────────────────────────
def pick_diverse_tickers(rng, n=13):
    """Pick n tickers spread across at least 6 sectors."""
    sectors = list(DIVERSE_POOL.keys())
    rng.shuffle(sectors)
    chosen = []
    # At least 1 from each of the first 6 sectors
    for sector in sectors[:6]:
        pool = [t for t in DIVERSE_POOL[sector] if t not in chosen]
        if pool:
            chosen.append(rng.choice(pool))
    # Fill remaining slots from the whole pool
    flat = [t for s in sectors for t in DIVERSE_POOL[s] if t not in chosen]
    rng.shuffle(flat)
    chosen.extend(flat[:n - len(chosen)])
    return chosen[:n]

# ── Account generation ─────────────────────────────────────────────────────────
rng_uuids = random.Random(SEED)
account_ids = [str(uuid.UUID(int=rng_uuids.getrandbits(128))) for _ in range(NUM_ACCOUNTS)]


def generate_normal_account(idx, rng):
    acct    = account_ids[idx]
    n_hold  = rng.randint(12, 15)
    tickers = pick_diverse_tickers(rng, n_hold)
    net     = {t: 0 for t in tickers}

    # PRICE events (with anomaly overrides)
    stale_cutoff = 30  # day offset: ~Jan 23
    for t in tickers:
        if idx in STALE_PRICE_IDXS and t in tickers[:2]:
            # No price updates after first 30 days → stale
            add_price_events(acct, t, MONDAYS, max_day_offset=stale_cutoff)
        elif idx == NO_PRICE_IDX and t in tickers[:2]:
            pass  # Intentionally omit ALL price events for these 2 tickers
        else:
            add_price_events(acct, t, MONDAYS)

    # Trade events
    trades_per_week = (4, 6) if idx == HIGH_CHURN_IDX else (1, 3)

    for wk in WEEKS:
        n = rng.randint(*trades_per_week)
        trade_days = sorted(rng.sample(wk, min(n, len(wk))))
        for tday in trade_days:
            t = rng.choice(tickers)
            if rng.random() < 0.75 or net[t] < 5:
                shares = rng.choice([5, 10, 15, 20, 25, 50])
                add_buy(acct, t, tday.replace(hour=10, minute=30), shares)
                net[t] += shares
            else:
                max_sell = int(net[t] * 0.5)
                candidates = [s for s in [5, 10, 15, 20] if s <= max_sell]
                if candidates:
                    shares = rng.choice(candidates)
                    add_sell(acct, t, tday.replace(hour=14, minute=0), shares)
                    net[t] -= shares

    # Oversell injection: force one ticker negative
    if idx == OVERSELL_IDX:
        bad = tickers[-1]
        held = net[bad]
        oversell = held + rng.randint(10, 30)
        bad_day = TRADING_DAYS[-5]
        add_sell(acct, bad, bad_day.replace(hour=11, minute=15), oversell)
        net[bad] -= oversell   # goes negative intentionally

    return net


def generate_special_account(idx, tickers_list, rng, all_from_universe=SP500_TICKERS,
                             trades_range=(1, 3)):
    """Generic generator for tech-heavy, small-cap, energy-only accounts."""
    acct    = account_ids[idx]
    tickers = list(tickers_list)
    net     = {t: 0 for t in tickers}

    for t in tickers:
        add_price_events(acct, t, MONDAYS)

    for wk in WEEKS:
        n = rng.randint(*trades_range)
        trade_days = sorted(rng.sample(wk, min(n, len(wk))))
        for tday in trade_days:
            t = rng.choice(tickers)
            if rng.random() < 0.78 or net[t] < 5:
                shares = rng.choice([5, 10, 20, 25, 50])
                add_buy(acct, t, tday.replace(hour=10, minute=0), shares)
                net[t] += shares
            else:
                max_sell = int(net[t] * 0.4)
                candidates = [s for s in [5, 10, 15] if s <= max_sell]
                if candidates:
                    shares = rng.choice(candidates)
                    add_sell(acct, t, tday.replace(hour=14, minute=0), shares)
                    net[t] -= shares
    return net

# ── Run generation ─────────────────────────────────────────────────────────────
NORMAL_COUNT = NUM_ACCOUNTS - 3   # 0..21 inclusive

# Normal accounts
for idx in range(NORMAL_COUNT):
    rng = random.Random(SEED + idx + 100)
    generate_normal_account(idx, rng)

# Tech-heavy account (account idx 22)
rng_tech = random.Random(SEED + 200)
tech_rng_tickers = rng_tech.sample(TECH_ONLY, 11)
generate_special_account(TECH_HEAVY_IDX, tech_rng_tickers, rng_tech, trades_range=(2, 4))

# Small-cap (Russell 2000) account (account idx 23)
rng_sc = random.Random(SEED + 300)
generate_special_account(SMALL_CAP_IDX, list(SMALL_CAP_TICKERS.keys()), rng_sc, trades_range=(1, 3))

# Energy-only account (account idx 24)
rng_en = random.Random(SEED + 400)
generate_special_account(ENERGY_ONLY_IDX, ENERGY_ONLY, rng_en, trades_range=(1, 2))

# ── Sort and write CSV ─────────────────────────────────────────────────────────
rows.sort(key=lambda r: (r["event_ts"], r["account_id"]))

FIELDNAMES = [
    "account_id", "ticker_symbol", "event_ts", "event_type",
    "shares", "price_per_share", "currency", "source",
]

out_path = Path(__file__).parent / "portfolio_event_ledger_500.csv"
with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)

# ── Summary report ─────────────────────────────────────────────────────────────
from collections import Counter
event_counter = Counter(r["event_type"] for r in rows)
acct_counter  = Counter(r["account_id"] for r in rows)

print(f"{'─'*60}")
print(f"Generated {len(rows):,} total rows → {out_path.name}")
print(f"{'─'*60}")
print(f"  Date range : {START_DATE.date()} → {ANCHOR_DATE.date()} ({TOTAL_DAYS} days)")
print(f"  Accounts   : {len(set(r['account_id'] for r in rows))}")
print(f"  Tickers    : {len(set(r['ticker_symbol'] for r in rows))} unique")
print(f"  BUY events : {event_counter['BUY']:,}")
print(f"  SELL events: {event_counter['SELL']:,}")
print(f"  PRICE evts : {event_counter['PRICE']:,}")
print()
print(f"Embedded detectable issues:")
print(f"  [4]  OVERSELL     → {account_ids[OVERSELL_IDX]}")
print(f"  [7]  STALE PRICE  → {account_ids[7]}")
print(f"  [10] NO PRICE     → {account_ids[NO_PRICE_IDX]}")
print(f"  [14] STALE PRICE  → {account_ids[14]}")
print(f"  [16] HIGH CHURN   → {account_ids[HIGH_CHURN_IDX]}")
print(f"  [22] TECH HEAVY   → {account_ids[TECH_HEAVY_IDX]}")
print(f"  [23] SMALL CAP    → {account_ids[SMALL_CAP_IDX]}")
print(f"  [24] ENERGY ONLY  → {account_ids[ENERGY_ONLY_IDX]}")
print()
print(f"Rows per account (min/max): "
      f"{min(acct_counter.values())} / {max(acct_counter.values())}")
