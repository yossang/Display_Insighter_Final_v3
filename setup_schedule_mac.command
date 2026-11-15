#!/bin/bash
# ==============================================================================
# Display Foresight - macOS/Linux 매일 새벽 04:00 (KST) Crontab 스케줄 자동 등록기
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_SH="${PROJECT_DIR}/run_pipeline.sh"
chmod +x "${PIPELINE_SH}"

CRON_LINE="0 4 * * * ${PIPELINE_SH}"

(crontab -l 2>/dev/null | grep -v "${PIPELINE_SH}"; echo "CRON_TZ=Asia/Seoul"; echo "${CRON_LINE}") | crontab -

echo "======================================================================"
echo "✅ 매일 한국시간(Asia/Seoul) 새벽 04:00 자동 실행 스케줄이 등록되었습니다!"
echo "실행 대상: ${PIPELINE_SH}"
echo "======================================================================"
crontab -l
