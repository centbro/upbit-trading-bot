@echo off
cd /d "%~dp0"
title 업비트 자동매매 봇

echo [INFO] 가상환경 활성화 시도...
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo [INFO] 서버 시작 중... (http://localhost:8000)
python run.py

echo.
echo [INFO] 서버가 종료되었습니다.
pause
