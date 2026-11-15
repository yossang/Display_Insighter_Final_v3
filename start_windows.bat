@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

echo ============================================================================
echo   [Display Foresight] B2B 디스플레이 기술·특허 전략 인텔리전스 대시보드
echo   Windows 원클릭 실행기 (start_windows.bat)
echo ============================================================================
echo.

cd /d "%~dp0"

REM 1. Python 설치 확인
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [오류] Python이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치해 주세요.
    pause
    exit /b 1
)

REM 2. .env 파일이 없으면 .env.example 복사
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [안내] 기본 .env 설정 파일이 생성되었습니다. (대시보드 좌측 사이드바에서 API 키를 언제든 등록할 수 있습니다.)
    )
)

REM 3. 필수 패키지 자동 확인 및 설치
python -c "import streamlit, pandas, plotly, requests, bs4, openai, dotenv" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [설치] 필수 파이썬 패키지를 설치합니다...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
)

REM 4. DB 초기화 보장
python init_db.py

echo.
echo [실행] Display Foresight 대시보드를 시작합니다...
echo 브라우저가 자동으로 열리지 않으면 http://localhost:8501 로 접속하세요.
echo (대시보드 내장 스케줄러가 매일 한국시간 새벽 04:00에 자동 수집을 수행합니다.)
echo.

python -m streamlit run main.py --server.port 8501 --server.address 0.0.0.0

pause
