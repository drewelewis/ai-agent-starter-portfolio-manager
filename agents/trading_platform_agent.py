"""
Trading Platform Agent
Specialized agent for portfolio event ledger queries and trade operations.
Built with Microsoft Agent Framework + AzureOpenAIChatClient (direct model call,
no hosted Foundry agent required — avoids agents/write permission).
"""

import os
from azure.identity import DefaultAzureCredential
from agent_framework import ChatAgent, ai_function, use_function_invocation
from agent_framework.azure import AzureOpenAIChatClient

# use_function_invocation is a class decorator — apply it to the client class
FunctionCallingClient = use_function_invocation(AzureOpenAIChatClient)

from tools.trading_platform_tool import (
    list_all_accounts,
    get_events_by_account,
    get_events_by_ticker,
    get_portfolio_summary,
    get_all_portfolio_summaries,
    get_events_by_account_ticker,
    get_account_analysis_context,
    get_latest_price,
    get_trade_history,
    run_query,
    insert_trade_event,
    check_database_health,
)

AGENT_NAME = "TradingPlatformAgent"
AGENT_DESCRIPTION = (
    "A specialized agent for querying and managing a portfolio event ledger. "
    "Handles BUY/SELL trade history, market price observations, portfolio "
    "summaries, and event insertion for multiple accounts and tickers."
)

AGENT_INSTRUCTIONS = """
You are the Trading Platform Agent, an expert in portfolio management and
financial trade data analysis.

You have access to a portfolio event ledger database that stores BUY, SELL,
and PRICE events for multiple accounts and equity tickers.

Your responsibilities:
- List all distinct accounts in the ledger
- Retrieve ALL account positions in one call for cross-account risk scanning
- Retrieve and summarize portfolio events for accounts and tickers
- Calculate net positions and cost basis for holdings
- Report the latest observed market prices for tickers
- Show trade history (BUY/SELL) filtered by account, event type, or date range
- Drill into a specific account+ticker position
- Execute custom read-only SQL queries for complex aggregations
- Insert new trade or price events when requested
- Check database connectivity health

Guidelines:
- Always confirm the account_id or ticker_symbol before running queries
- Format numbers clearly: shares to 4 decimal places, prices to 2 decimal places
- When presenting portfolio summaries, clearly distinguish net shares,
  net cost basis, and the last observed market price
- If a query returns no results, say so clearly rather than guessing
- Never fabricate trade data – only report what the database returns
"""


async def create_trading_platform_agent() -> ChatAgent:
    """
    Factory function to create and return an initialized TradingPlatformAgent.

    Uses AzureOpenAIChatClient (direct model endpoint) so no Foundry
    agents/write permission is required. Reads:
        AZURE_OPENAI_API_ENDPOINT  - Azure OpenAI / Foundry endpoint
        MODEL_DEPLOYMENT_NAME      - Model deployment name (e.g. gpt-4.1)

    Authentication via DefaultAzureCredential (managed identity in Azure,
    az login locally).

    Returns:
        A configured ChatAgent with all trading platform tools attached.
    """
    credential = DefaultAzureCredential()

    client = FunctionCallingClient(
        endpoint=os.getenv("AZURE_OPENAI_API_ENDPOINT"),
        deployment_name=os.getenv("MODEL_DEPLOYMENT_NAME"),
        credential=credential,
    )

    agent = ChatAgent(
        chat_client=client,
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        instructions=AGENT_INSTRUCTIONS,
        tools=[
            ai_function(list_all_accounts),
            ai_function(get_all_portfolio_summaries),
            ai_function(get_portfolio_summary),
            ai_function(get_account_analysis_context),
            ai_function(get_events_by_account),
            ai_function(get_events_by_account_ticker),
            ai_function(get_events_by_ticker),
            ai_function(get_latest_price),
            ai_function(get_trade_history),
            ai_function(run_query),
            ai_function(insert_trade_event),
            ai_function(check_database_health),
        ],
    )

    return agent

