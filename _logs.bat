@echo off
REM View logs from AI Agent Starter container
echo 📜 Viewing AI Agent Starter logs...
echo Press Ctrl+C to stop
echo.

docker logs -f ai-agent-starter
