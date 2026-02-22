"""
Main Entry Point - Trading Platform API
Starts the Trading Platform FastAPI server with Microsoft Agent Framework
"""

import os
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv(override=True)

if __name__ == "__main__":
    import uvicorn

    host   = os.getenv("SERVER_HOST",   "0.0.0.0")
    port   = int(os.getenv("SERVER_PORT", "8989"))
    reload = os.getenv("SERVER_RELOAD", "false").lower() == "true"

    print("=" * 55)
    print(" Trading Platform API")
    print("=" * 55)
    print(f" Framework : Microsoft Agent Framework")
    print(f" Host      : {host}:{port}")
    print(f" Docs      : http://localhost:{port}/docs")
    print(f" Health    : http://localhost:{port}/health")
    print("=" * 55)

    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        timeout_keep_alive=60,
    )
