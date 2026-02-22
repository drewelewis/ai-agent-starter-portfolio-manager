@echo off
REM Build Docker image for AI Agent Starter
echo 🐳 Building AI Agent Starter Docker image...
echo.

docker build -t drewl/ai-agent-starter-portfolio-manager -f dockerfile .

if %errorlevel% neq 0 (
    echo ❌ Docker build failed!
    exit /b 1
)

echo.
echo ✅ Build completed!
echo 📦 Image: drewl/ai-agent-starter-portfolio-manager
echo.
echo 💡 Next steps:
echo    - Run: docker-compose up -d
echo    - Or: _up.bat
echo    - Push to Docker Hub: docker push drewl/ai-agent-starter-portfolio-manager
