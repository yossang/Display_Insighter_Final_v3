#!/bin/bash
# ==============================================================================
# Display Foresight - macOS/Linux 원클릭 대시보드 실행기 (start_mac.command)
# Finder에서 더블클릭하면 즉시 대시보드가 실행되고 브라우저가 열립니다.
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}" || exit 1

echo "======================================================================"
echo "  🔬 Display Foresight - B2B 디스플레이 기술·특허 인텔리전스 대시보드"
echo "======================================================================"

# 1. 가상환경 활성화 (있는 경우)
if [ -f "${PROJECT_DIR}/.venv/bin/activate" ]; then
    source "${PROJECT_DIR}/.venv/bin/activate"
elif [ -f "${PROJECT_DIR}/venv/bin/activate" ]; then
    source "${PROJECT_DIR}/venv/bin/activate"
fi

# 2. .env 파일 자동 준비
if [ ! -f "${PROJECT_DIR}/.env" ] && [ -f "${PROJECT_DIR}/.env.example" ]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    echo "[안내] .env 파일이 생성되었습니다. 대시보드 좌측 사이드바에서 API 키를 나중에 등록하실 수 있습니다."
fi

# 3. 필수 라이브러리 점검 및 설치
if ! python3 -c "import streamlit, pandas, plotly, requests, bs4, openai, dotenv" >/dev/null 2>&1; then
    echo "[설치] 필수 파이썬 패키지를 확인 및 설치합니다..."
    python3 -m pip install -r requirements.txt
fi

# 4. DB 초기화 보장
python3 init_db.py

echo ""
echo "[실행] http://localhost:8501 에서 대시보드를 시작합니다..."
echo "(내장 스케줄러가 매일 한국시간 새벽 04:00에 자동 수집·분석을 수행합니다.)"
echo ""

python3 -m streamlit run main.py --server.port 8501 --server.address 0.0.0.0
