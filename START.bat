@echo off
echo HealthGuard v10 - Starting...
echo.
cd /d "%~dp0"
cd backend
echo Server starting at http://localhost:8000
echo Open your browser to http://localhost:8000
echo Press Ctrl+C to stop
echo.
python server.py
pause
