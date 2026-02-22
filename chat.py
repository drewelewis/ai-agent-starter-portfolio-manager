"""
Trading Platform Agent - Interactive Chat CLI

Run:
    python chat.py

Built-in commands:
    help      - Show available commands and example queries
    status    - Show agent and database health
    clear     - Reset conversation history (new thread)
    quit/exit - Exit the CLI
"""

import asyncio
from dotenv import load_dotenv
from agent_framework import AgentThread

from agents.trading_platform_agent import create_trading_platform_agent
from operations.trading_platform_operations import TradingPlatformOperations

load_dotenv(override=True)

BANNER = """
===========================================================
  Trading Platform Agent - Interactive Chat
===========================================================
  Ask anything about your portfolio, trades, or prices.

  Examples:
    "Show portfolio summary for account A100"
    "What is the latest price for MSFT?"
    "Show all BUY trades for account A101"
    "Insert a BUY of 5 AAPL shares at 225.50 for A100"
    "How many shares of GOOG does A102 hold?"

  Commands: help | status | clear | quit
===========================================================
"""

HELP_TEXT = """
Commands:
  help    - Show this help message
  status  - Agent and database health check
  clear   - Reset conversation (start a new thread)
  quit    - Exit

Example queries:
  Portfolio:
    "Show portfolio summary for account A100"
    "What are all events for account A101?"
    "Show BUY history for A100"
    "Show SELL history for A102"

  Prices:
    "What is the latest price for MSFT?"
    "Get the last observed price for AAPL"

  Tickers:
    "Show all events for TSLA"
    "List all AMZN trades across accounts"

  Insert events:
    "Record a BUY of 10 MSFT shares at 420.00 for account A100"
    "Insert a PRICE event for GOOG at 185.50"
"""


async def main():
    print(BANNER)

    # ── Initialize ─────────────────────────────────────────────────────────────
    print("Initializing Trading Platform Agent ...", end=" ", flush=True)
    try:
        agent = await create_trading_platform_agent()
        print("ready.")
    except Exception as e:
        print(f"\nFailed to initialize agent: {e}")
        return

    print("Connecting to database ...", end=" ", flush=True)
    db = TradingPlatformOperations()
    try:
        await db.initialize()
        print("connected.\n")
    except Exception as e:
        print(f"\nWarning: database unavailable — {e}")
        print("Structured DB commands will fail; agent chat is still available.\n")

    thread = AgentThread()

    # ── Chat loop ──────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        cmd = user_input.lower()

        # ── Built-in commands ──────────────────────────────────────────────────
        if cmd in ("quit", "exit", "bye"):
            print("Goodbye!")
            break

        if cmd == "help":
            print(HELP_TEXT)
            continue

        if cmd == "clear":
            thread = AgentThread()
            print("Conversation history cleared — new thread started.\n")
            continue

        if cmd == "status":
            db_ok = await db.health_check() if db.pool else False
            print(f"  Agent    : ready")
            print(f"  Database : {'connected' if db_ok else 'disconnected'}\n")
            continue

        # ── Agent response ─────────────────────────────────────────────────────
        print("\nAgent: ", end="", flush=True)
        try:
            response = await agent.run(user_input, thread=thread)
            print(response.text or "(no response)")
        except Exception as e:
            print(f"Error: {e}")
        print()

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())

