@echo off
chcp 65001 > nul
title Display Foresight Tracker - 원클릭 구동기
cd /d "%~dp0"

echo [1/3] 파이썬 가상환경(venv) 세팅 중...
if not exist venv ( python -m venv venv )
call "venv\Scripts\activate.bat"

echo [2/3] 필수 패키지 점검 중 (최초 1회 1~2분 소요)...
python -m pip install -r requirements.txt > nul 2>&1

echo [3/3] 대시보드를 띄웁니다!
if not exist .env (
    if exist .env.example copy /y .env.example .env > nul
)
python init_db.py
python -m streamlit run main.py
pause
