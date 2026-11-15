@echo off
chcp 65001 > nul
title Display Foresight Tracker - 원클릭 구동기
cd /d "%~dp0"

echo [1/3] 파이썬 가상환경(venv) 세팅 중...
if not exist venv ( python -m venv venv )
call venv\Scriptsctivate.bat

echo [2/3] 필수 패키지 점검 중 (최초 1회 1~2분 소요)...
pip install -r requirements.txt > nul 2>&1

echo [3/3] 대시보드를 띄웁니다!
if not exist .env (
    echo OPENAI_API_KEY=test > .env
    echo KIPRIS_API_KEY=test >> .env
)
python init_db.py
streamlit run main.py
