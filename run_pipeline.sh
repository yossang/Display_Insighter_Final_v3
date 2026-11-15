#!/bin/bash
# ==============================================================================
# Display Foresight - Daily Batch Pipeline Runner (run_pipeline.sh)
#
# 설명:
#   매일 새벽 특허(KIPRIS) 및 뉴스(Google News RSS) 원문을 수집하고,
#   gpt-4o-mini 기반 동적 키워드 추출 및 gpt-4o 기반 델타/이상기류 분석을
#   동기식(Synchronous)으로 순차 실행하는 B2B 프로덕션 배치 스크립트입니다.
#   모든 표준 출력(stdout)과 표준 에러(stderr)는 프로젝트 루트의 `batch_log.txt`에 누적 기록됩니다.
# ==============================================================================

set -u

# 1. 프로젝트 루트 경로 확인 및 이동 (스크립트 파일 위치 기준 절대 경로 계산)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}" || {
    echo "[ERROR] 프로젝트 디렉토리 이동 실패: ${PROJECT_DIR}" >&2
    exit 1
}

LOG_FILE="${PROJECT_DIR}/batch_log.txt"

# 가상환경(.venv 또는 venv)이 존재하면 자동 활성화, 없으면 시스템 python3 / python 탐색
if [ -f "${PROJECT_DIR}/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/.venv/bin/activate"
elif [ -f "${PROJECT_DIR}/venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/venv/bin/activate"
fi

# python 명령어가 없을 경우(macOS 기본 환경 등) python3 심볼릭 폴백을 PATH/함수에 등록하여
# `python collectors/fetch_patents.py` 형태의 호출이 어떤 환경에서도 안전하게 동작하도록 보장
if ! command -v python >/dev/null 2>&1; then
    if command -v python3 >/dev/null 2>&1; then
        python() {
            python3 "$@"
        }
    fi
fi

# 2. 모든 출력 및 에러를 batch_log.txt에 누적 기록 (>> ... 2>&1)
{
    echo "======================================================================"
    echo "[START] Display Foresight 일일 배치 파이프라인 시작: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Working Directory: ${PROJECT_DIR}"
    echo "======================================================================"

    # 사전 점검: DB 파일이 없을 경우 초기화 보장
    if [ ! -f "${PROJECT_DIR}/display_foresight.db" ]; then
        echo "[INIT] display_foresight.db 파일이 없어 초기화를 수행합니다..."
        python init_db.py
    fi

    # Step 1: KIPRIS 특허 수집
    echo ""
    echo "[STEP 1/4] KIPRIS 특허 수집 시작 (collectors/fetch_patents.py) - $(date '+%Y-%m-%d %H:%M:%S')"
    python collectors/fetch_patents.py
    STEP1_STATUS=$?
    echo "[STEP 1/4] 종료 코드: ${STEP1_STATUS}"

    # Step 2: Google News RSS 수집 및 순수 본문 발췌
    echo ""
    echo "[STEP 2/4] Google News 수집 시작 (collectors/fetch_news.py) - $(date '+%Y-%m-%d %H:%M:%S')"
    python collectors/fetch_news.py
    STEP2_STATUS=$?
    echo "[STEP 2/4] 종료 코드: ${STEP2_STATUS}"

    # Step 3: gpt-4o-mini 기반 동적 기술 키워드 추출 및 트렌드 업데이트
    echo ""
    echo "[STEP 3/4] 동적 기술 키워드 추출 시작 (analyzers/extract_keywords.py) - $(date '+%Y-%m-%d %H:%M:%S')"
    python analyzers/extract_keywords.py
    STEP3_STATUS=$?
    echo "[STEP 3/4] 종료 코드: ${STEP3_STATUS}"

    # Step 4: gpt-4o 기반 델타(Delta) 3줄 요약 및 이상 기류(Anomaly) 탐지
    echo ""
    echo "[STEP 4/5] 심층 델타 분석 및 이상 기류 탐지 시작 (analyzers/analyze_delta.py) - $(date '+%Y-%m-%d %H:%M:%S')"
    python analyzers/analyze_delta.py
    STEP4_STATUS=$?
    echo "[STEP 4/5] 종료 코드: ${STEP4_STATUS}"

    # Step 5: 일간(Daily) 및 주간(Weekly - 월요일 자동 감지) 종합 요약 생성
    echo ""
    echo "[STEP 5/5] 일간/주간 Executive Summary 생성 시작 (analyzers/executive_summary.py) - $(date '+%Y-%m-%d %H:%M:%S')"
    python analyzers/executive_summary.py
    STEP5_STATUS=$?
    echo "[STEP 5/5] 종료 코드: ${STEP5_STATUS}"

    echo ""
    echo "======================================================================"
    echo "[END] Display Foresight 일일 배치 파이프라인 완료: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Summary Exit Codes -> Step1:${STEP1_STATUS}, Step2:${STEP2_STATUS}, Step3:${STEP3_STATUS}, Step4:${STEP4_STATUS}, Step5:${STEP5_STATUS}"
    echo "======================================================================"
    echo ""
} >> "${LOG_FILE}" 2>&1
