import asyncio, os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def main():
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        database=os.getenv("POSTGRES_DB", "postgres"),
        ssl=os.getenv("POSTGRES_SSL_MODE", "require"),
    )
    rows = await conn.fetch("""
        SELECT
            account_id,
            COUNT(*) AS total,
            SUM(CASE WHEN event_type='BUY'   THEN 1 ELSE 0 END) AS buys,
            SUM(CASE WHEN event_type='SELL'  THEN 1 ELSE 0 END) AS sells,
            SUM(CASE WHEN event_type='PRICE' THEN 1 ELSE 0 END) AS prices,
            COUNT(DISTINCT ticker_symbol) AS tickers
        FROM portfolio_event_ledger
        GROUP BY account_id
        ORDER BY account_id
    """)
    grand_total = 0
    print(f"  {'account_id':10s}  {'total':>6}  {'BUY':>5}  {'SELL':>5}  {'PRICE':>6}  {'tickers':>7}")
    print("  " + "-" * 55)
    for r in rows:
        print(f"  {r['account_id']:10s}  {r['total']:>6}  {r['buys']:>5}  {r['sells']:>5}  {r['prices']:>6}  {r['tickers']:>7}")
        grand_total += r["total"]
    print("  " + "-" * 55)
    print(f"  {'TOTAL':10s}  {grand_total:>6}")
    await conn.close()

asyncio.run(main())
