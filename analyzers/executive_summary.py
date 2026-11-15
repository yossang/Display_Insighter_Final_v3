"""
Display Foresight - Daily & Weekly Executive Summary Engine (analyzers/executive_summary.py)

본 모듈은 매일 새벽 배치 실행 시:
1) 당일 수집·분석된 `analysis_data`를 종합하여 '오늘의 종합 브리핑(`summary_type='daily'`)'을 무조건 생성하여
   `executive_summary` 테이블에 저장하고,
2) 파이썬 `datetime` 모듈로 오늘이 '월요일(Monday, `weekday() == 0`)'인지 체크하여,
   월요일 새벽인 경우 지난 7일간의 `analysis_data` 전체를 조회해 LGD 최고 기술 전략 참모(CSO) 관점의
   '전주 심층 요약(`summary_type='weekly'`)'을 `gpt-4o`로 추가 생성하여 `executive_summary` 테이블에 적재합니다.
"""

from datetime import datetime, timedelta
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트 경로 보장
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.db_helper import (
    get_dashboard_data,
    get_executive_summary,
    has_newer_analysis_since,
    load_project_env,
    upsert_executive_summary,
)

load_project_env()

# OpenAI 공식 SDK 임포트
try:
    from openai import OpenAI, OpenAIError  # type: ignore

    HAS_OPENAI_SDK = True
except ImportError:
    OpenAI = None  # type: ignore
    OpenAIError = Exception  # type: ignore
    HAS_OPENAI_SDK = False

# 로깅 설정
logger = logging.getLogger("DisplayForesight.ExecutiveSummary")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# =====================================================================
# 1. LLM 모델 및 일간(Daily) / 주간(Weekly) 프롬프트 상수 정의
# =====================================================================

DEFAULT_EXEC_MODEL: str = "gpt-4o"

# [일간 종합 브리핑 프롬프트]
DAILY_SUMMARY_SYSTEM_PROMPT: str = (
    "너는 글로벌 디스플레이 산업 및 기술전략 최고 자문위원이다. "
    "오늘 수집된 개별 기술 동향들을 종합하여, 디스플레이 산업의 거시적 흐름과 시사점을 "
    "3문단의 '오늘의 종합 브리핑' 마크다운(Markdown)으로 도출하라. "
    "각 문단은 빈 줄(\\n\\n)로 명확히 구분하고, "
    "1문단은 핵심 기술/특허 R&D 트렌드, "
    "2문단은 패널사·세트사 설비투자 및 공급망 역학(이상 기류 포함), "
    "3문단은 상품기획 및 중장기 로드맵 전략 시사점을 중심으로 밀도 높게 작성하라."
)

# [Phase 6-2 주간 심층 요약 프롬프트 - 마스터 컨텍스트 지정 프롬프트]
WEEKLY_SUMMARY_SYSTEM_PROMPT: str = (
    "너는 LGD 최고 기술 전략 참모(CSO)다. "
    "지난 1주간(7일) 누적된 경쟁사 및 세트사 데이터를 바탕으로 "
    "[주간 글로벌 기술/투자 트렌드], [주요 기업 주간 핵심 동향 3가지], "
    "[LGD 중장기 위협 요인 및 전략적 액션 아이템]을 3단락의 마크다운으로 작성하라."
)


# =====================================================================
# 2. 기간별 분석 데이터(analysis_data) 조회 헬퍼
# =====================================================================


def fetch_analyzed_records_in_period(
    start_date_str: str,
    end_date_str: str,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    `start_date_str` ~ `end_date_str`(YYYY-MM-DD) 기간 내에 수집/발행되거나 분석된
    `analysis_data` + `raw_data` 조인 레코드들을 조회합니다.
    """
    all_records = get_dashboard_data(date=None, db_path=db_path)
    matched: List[Dict[str, Any]] = []
    for row in all_records:
        delta_txt = (row.get("delta_summary") or "").strip()
        if not delta_txt:
            continue
        pub_d = (row.get("published_date") or "")[:10]
        coll_d = (row.get("collected_at") or "")[:10]
        anal_d = (row.get("analyzed_at") or "")[:10]

        if any(start_date_str <= d <= end_date_str for d in (pub_d, coll_d, anal_d) if d):
            matched.append(row)
    return matched


def _format_records_for_prompt(records: List[Dict[str, Any]], max_items: int = 20) -> str:
    """
    개별 `analysis_data` 레코드 리스트를 `gpt-4o` 입력 프롬프트용 텍스트로 압축 포맷팅합니다.
    (토큰 최적화: 원문 `content`는 제외하고 핵심 메타데이터와 `delta_summary`만 최대 `max_items`건 전달)
    """
    lines: List[str] = []
    for idx, rec in enumerate(records[:max_items], start=1):
        src_label = "특허" if rec.get("source_type") == "patent" else "뉴스"
        comp = rec.get("company", "Unknown")
        pub_date = rec.get("published_date", "")
        title = rec.get("title", "")
        kw_str = ", ".join(rec.get("tech_keywords_list") or [])
        delta_txt = (rec.get("delta_summary") or "").strip()
        anom_str = (
            f" | 🚨[이상기류]: {rec.get('anomaly_description')}"
            if rec.get("anomaly_flag")
            else ""
        )
        lines.append(
            f"{idx}. [{pub_date}|{comp}|{src_label}] {title} ({kw_str}){anom_str}\n"
            f"{delta_txt}"
        )
    return "\n\n".join(lines)


# =====================================================================
# 3. 일간(Daily) 및 주간(Weekly) 종합 요약 생성 함수 (Smart Skip 토큰 방어 적용)
# =====================================================================


def generate_daily_executive_summary(
    target_date: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EXEC_MODEL,
    db_path: Optional[Path] = None,
    force: bool = False,
) -> Optional[str]:
    """
    매일 배치 실행 시 호출되어 오늘의 종합 브리핑(`summary_type='daily'`)을 생성하고
    `executive_summary` 테이블에 저장합니다.
    (Smart Skip: 해당 날짜의 요약이 이미 존재하고 이후 신규 분석 데이터가 없으면 `gpt-4o` 재호출을 생략합니다.)
    """
    effective_date = (target_date or datetime.now().strftime("%Y-%m-%d")).strip()[:10]

    if not force:
        existing = get_executive_summary(summary_type="daily", date=effective_date, db_path=db_path)
        if (
            existing
            and existing.get("date") == effective_date
            and existing.get("content")
            and not has_newer_analysis_since(existing.get("updated_at", ""), db_path=db_path)
        ):
            logger.info(
                f"[{effective_date}] 최신 분석 데이터가 반영된 일간 브리핑(daily)이 이미 존재하여 LLM 호출을 스킵합니다 (Zero-Token 방어)."
            )
            return existing["content"]

    if not HAS_OPENAI_SDK:
        logger.error("`openai` 패키지가 설치되어 있지 않습니다.")
        return None

    resolved_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    if not resolved_key or resolved_key == "your_openai_api_key_here":
        logger.error("OPENAI_API_KEY가 설정되지 않아 일간 종합 브리핑 생성을 건너뜁니다.")
        return None

    day_records = get_dashboard_data(date=effective_date, db_path=db_path)
    day_records = [r for r in day_records if (r.get("delta_summary") or "").strip()]

    # 당일 날짜로 매칭된 데이터가 없을 경우 가장 최근 분석 날짜 데이터를 폴백 사용
    if not day_records:
        all_records = get_dashboard_data(date=None, db_path=db_path)
        valid_records = [r for r in all_records if (r.get("delta_summary") or "").strip()]
        if not valid_records:
            logger.info("일간 종합 브리핑(daily)을 생성할 분석 데이터가 없습니다.")
            return None
        effective_date = (valid_records[0].get("published_date") or effective_date)[:10]
        day_records = [r for r in valid_records if (r.get("published_date") or "")[:10] == effective_date]

    formatted_items = _format_records_for_prompt(day_records, max_items=15)
    user_prompt = (
        f"[브리핑 기준일]: {effective_date} (총 {len(day_records)}건)\n\n"
        f"{formatted_items}\n\n"
        "위 개별 기술 동향들을 종합하여, 디스플레이 산업의 거시적 흐름과 시사점을 3문단의 '오늘의 종합 브리핑'으로 도출하라."
    )

    try:
        client = OpenAI(api_key=resolved_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DAILY_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.25,
            max_tokens=850,
        )
        content = ""
        if response.choices and response.choices[0].message:
            content = (response.choices[0].message.content or "").strip()

        if not content:
            logger.warning(f"[{effective_date}] LLM이 빈 일간 종합 브리핑을 반환했습니다.")
            return None

        saved = upsert_executive_summary(
            date=effective_date,
            summary_type="daily",
            content=content,
            db_path=db_path,
        )
        return content if saved else None

    except Exception as exc:
        logger.error(f"일간 종합 브리핑(daily) 생성 중 오류 발생: {exc}", exc_info=True)
        return None


def generate_weekly_executive_summary(
    reference_dt: Optional[datetime] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EXEC_MODEL,
    db_path: Optional[Path] = None,
    force: bool = False,
) -> Optional[str]:
    """
    지난 7일간의 `analysis_data`를 모두 SELECT하여 `gpt-4o`에 전달하고,
    LGD 최고 기술 전략 참모(CSO) 관점의 3단락 마크다운 주간 요약(`summary_type='weekly'`)을 생성해
    `executive_summary` 테이블에 저장합니다.
    (Smart Skip: 해당 주간 요약이 이미 최신 상태이면 `gpt-4o` 재호출을 생략합니다.)
    """
    ref_dt = reference_dt or datetime.now()
    end_date_str = ref_dt.strftime("%Y-%m-%d")
    start_date_str = (ref_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    if not force:
        existing = get_executive_summary(summary_type="weekly", date=end_date_str, db_path=db_path)
        if (
            existing
            and existing.get("date") == end_date_str
            and existing.get("content")
            and not has_newer_analysis_since(existing.get("updated_at", ""), db_path=db_path)
        ):
            logger.info(
                f"[{end_date_str}] 최신 분석 데이터가 반영된 주간 심층 요약(weekly)이 이미 존재하여 LLM 호출을 스킵합니다 (Zero-Token 방어)."
            )
            return existing["content"]

    if not HAS_OPENAI_SDK:
        logger.error("`openai` 패키지가 설치되어 있지 않습니다.")
        return None

    resolved_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    if not resolved_key or resolved_key == "your_openai_api_key_here":
        logger.error("OPENAI_API_KEY가 설정되지 않아 주간 심층 요약 생성을 건너뜁니다.")
        return None

    weekly_records = fetch_analyzed_records_in_period(
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        db_path=db_path,
    )

    # 만약 최근 7일 내 데이터가 없을 경우 DB 내 최근 적재된 상위 20건을 폴백으로 활용
    if not weekly_records:
        all_records = get_dashboard_data(date=None, db_path=db_path)
        weekly_records = [r for r in all_records if (r.get("delta_summary") or "").strip()][:20]
        if not weekly_records:
            logger.info("주간 심층 요약(weekly)을 생성할 지난 7일간의 분석 데이터가 없습니다.")
            return None

    formatted_items = _format_records_for_prompt(weekly_records, max_items=20)
    user_prompt = (
        f"[주간 브리핑 집계 기간]: {start_date_str} ~ {end_date_str} (총 {len(weekly_records)}건)\n\n"
        f"{formatted_items}\n\n"
        "위 지난 1주간(7일) 누적된 경쟁사 및 세트사 데이터를 바탕으로 "
        "[주간 글로벌 기술/투자 트렌드], [주요 기업 주간 핵심 동향 3가지], "
        "[LGD 중장기 위협 요인 및 전략적 액션 아이템]을 3단락의 마크다운으로 작성하라."
    )

    try:
        client = OpenAI(api_key=resolved_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": WEEKLY_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.25,
            max_tokens=1000,
        )
        content = ""
        if response.choices and response.choices[0].message:
            content = (response.choices[0].message.content or "").strip()

        if not content:
            logger.warning(f"[{end_date_str}] LLM이 빈 주간 심층 요약을 반환했습니다.")
            return None

        saved = upsert_executive_summary(
            date=end_date_str,
            summary_type="weekly",
            content=content,
            db_path=db_path,
        )
        return content if saved else None

    except Exception as exc:
        logger.error(f"주간 심층 요약(weekly) 생성 중 오류 발생: {exc}", exc_info=True)
        return None


def run_executive_summary_pipeline(
    current_dt: Optional[datetime] = None,
    force_weekly: bool = False,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EXEC_MODEL,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    [Phase 6-2 핵심 파이프라인]
    1. 매일 배치가 돌 때 '일간 요약(`summary_type='daily'`)'은 무조건 생성하여 `executive_summary`에 저장합니다.
    2. `datetime` 모듈의 `.weekday() == 0`을 통해 오늘이 '월요일(Monday)' 새벽인지 체크합니다.
    3. 월요일(또는 `force_weekly=True`)인 경우 지난 7일간의 `analysis_data`를 모두 SELECT하여
       `gpt-4o`에 전달하고 '주간 요약(`summary_type='weekly'`)'을 추가로 생성해 `executive_summary`에 저장합니다.

    Args:
        current_dt (Optional[datetime]): 기준 시각. 미지정 시 `datetime.now()`.
        force_weekly (bool): 월요일이 아니더라도 강제로 주간 요약을 생성할지 여부 (기본값: False).
        api_key (Optional[str]): OpenAI API 키.
        model (str): 사용할 LLM 모델명 (기본값: 'gpt-4o').
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        Dict[str, Any]: 실행 결과 요약 (`date`, `is_monday`, `daily_generated`, `weekly_generated`).
    """
    now_dt = current_dt or datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")
    is_monday: bool = now_dt.weekday() == 0  # Monday == 0

    result: Dict[str, Any] = {
        "date": today_str,
        "is_monday": is_monday,
        "daily_generated": False,
        "weekly_generated": False,
    }

    # 1. 일간 요약(daily)은 매일 무조건 생성
    logger.info(f"[{today_str}] 일간 종합 브리핑(summary_type='daily') 생성을 시작합니다.")
    daily_md = generate_daily_executive_summary(
        target_date=today_str,
        api_key=api_key,
        model=model,
        db_path=db_path,
    )
    result["daily_generated"] = bool(daily_md)

    # 2. 오늘이 월요일(Monday) 새벽인지 체크 -> 지난 7일 데이터 기반 주간 요약(weekly) 추가 생성
    if is_monday or force_weekly:
        logger.info(
            f"[{today_str}] 월요일 배치 감지(is_monday={is_monday}, force={force_weekly}) -> "
            f"지난 7일간의 analysis_data 기반 주간 심층 요약(summary_type='weekly') 생성을 시작합니다."
        )
        weekly_md = generate_weekly_executive_summary(
            reference_dt=now_dt,
            api_key=api_key,
            model=model,
            db_path=db_path,
        )
        result["weekly_generated"] = bool(weekly_md)
    else:
        logger.info(
            f"[{today_str}] 오늘은 월요일이 아니므로(weekday={now_dt.weekday()}) 주간 요약 생성은 스킵합니다."
        )

    return result


# 하위 호환성을 위한 별칭 함수
def generate_and_store_executive_summary(
    target_date: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_EXEC_MODEL,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    dt = (
        datetime.strptime(target_date[:10], "%Y-%m-%d")
        if target_date and len(target_date) >= 10
        else datetime.now()
    )
    res = run_executive_summary_pipeline(current_dt=dt, api_key=api_key, model=model, db_path=db_path)
    return "GENERATED" if res.get("daily_generated") else None


if __name__ == "__main__":
    summary_stats = run_executive_summary_pipeline()
    print(json.dumps(summary_stats, indent=2, ensure_ascii=False))
