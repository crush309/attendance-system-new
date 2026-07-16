@echo off
echo ========================================
echo   Attendance Analysis System
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
pip install -r requirements.txt -q

echo.
echo [2/2] Starting server...
echo.
echo ========================================
echo   URL: http://localhost:8080
echo   Admin: admin / admin123
echo   Press Ctrl+C to stop
echo ========================================
echo.

cd backend
python -m uvicorn app:app --host 0.0.0.0 --port 8080 --reload
pause
