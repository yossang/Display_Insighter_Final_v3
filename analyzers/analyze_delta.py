"""
Display Foresight - Deep Delta Analysis & Anomaly Detection Engine (analyzers/analyze_delta.py)

본 모듈은 Phase 3-1에서 기술 키워드(`tech_keywords`)가 추출되었으나 아직 심층 델타 요약(`delta_summary`)이
비어 있는 레코드를 조회하여, OpenAI의 `gpt-4o` 모델을 통해:
1) 과거 30일간의 기존 요약본(Past Context) 대비 기술적 진보/변경점(Delta)을 상품기획 관점에서 3줄 요약하고,
2) 해당 기업의 특허(R&D 의도) 흐름과 뉴스(설비투자/양산 액션)를 교차 검증하여 '이상 기류(Anomaly)'를 탐지합니다.

핵심 설계 원칙:
1. 토큰 최적화 (Summary-Only Past Context):
   - 과거 컨텍스트 구성 시 무거운 과거 원문(`content`)을 다시 불러오지 않고,
     동일 기업(`company`) 또는 겹치는 기술 키워드(`tech_keywords`)를 가진 최근 30일 이내의
     기존 `delta_summary`(및 출처 유형/제목)만 경량 조립하여 `gpt-4o` 입력 토큰을 획기적으로 절감합니다.
2. 단일 API 호출 & JSON 스키마 강제:
   - System Prompt 1(델타 분석)과 System Prompt 2(이상 기류 감지)를 통합하여 단 한 번의 `gpt-4o` 호출로
     `{"delta_summary": "...", "anomaly_flag": true/false, "anomaly_description": "..."}` JSON 객체를 반환받습니다.
3. 무중단 예외 격리 및 DB 업데이트:
   - 개별 문서 분석 중 API 오류나 `JSONDecodeError`가 발생해도 배치가 멈추지 않고 다음 문서로 진행하며,
     분석 완료된 결과는 `utils.db_helper.update_analysis_delta`를 통해 `analysis_data` 테이블에 반영합니다.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트 경로를 sys.path에 추가하여 상위 패키지(utils) 임포트 보장
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.db_helper import (
    get_connection,
    load_project_env,
    parse_and_normalize_keywords,
    update_analysis_delta,
    update_korean_translation,
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
logger = logging.getLogger("DisplayForesight.DeltaAnalyzer")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# =====================================================================
# 1. LLM 모델 및 통합 System Prompt 상수 정의 (gpt-4o / gpt-4o-mini)
# =====================================================================

DEFAULT_DELTA_MODEL: str = "gpt-4o"
DEFAULT_TRANSLATION_MODEL: str = "gpt-4o-mini"

# [Phase 7 신규] 영문 특허 요약서 & 독립 청구항 한국어 전문 번역 프롬프트
SYSTEM_PROMPT_ENGLISH_TRANSLATION: str = (
    "이 영문 특허의 요약서와 독립 청구항을 디스플레이 엔지니어들이 이해하기 쉬운 "
    "자연스러운 한국어 전문 용어로 번역하라.\n"
    "- 출력은 마크다운(Markdown) 형식으로 작성하되, `### 1. 발명의 명칭 (국문 번역)`, "
    "`### 2. 요약서 (Abstract 국문 번역)`, `### 3. 독립 청구항 (Independent Claims 국문 번역)` "
    "구조로 명확히 구분하여 가독성 높게 번역하라."
)

# [Phase 2 & 3 통합 업데이트] 자사(LG Display / LGD) 관점이 부여된 System Prompt 1 & 2 통합
SYSTEM_PROMPT_DELTA_AND_ANOMALY: str = """너는 LG Display(LGD)의 글로벌 디스플레이 산업 및 상품기획/기술전략 최고 전문가다.
아래 지침(자사 관점 기준점, System Prompt 1, System Prompt 2)을 동시에 수행하고, 반드시 지정된 JSON 객체 포맷으로만 응답하라.

[자사(LG Display) 기준점 원칙]
- 분석 대상 기업이 'LG Display' 또는 'LGD'인 경우, 절대 "경쟁사"라고 지칭하지 말고 **"당사(자사, LGD)"** 관점에서 당사의 R&D 성과, IP 방어력, 양산 경쟁력 및 전략적 의의를 요약하라.
- 분석 대상이 타 패널사(SDC, BOE, CSOT, Visionox, AUO, Innolux)이면 "경쟁 패널사" 관점에서 당사(LGD)에 미치는 위협/비교우위를 분석하고, 세트사(Apple, Samsung, Huawei)이면 "글로벌 고객사/세트사" 관점에서 당사 패널 수주 및 사양 대응 시사점을 도출하라.

[System Prompt 1: 델타(Delta) 분석]
과거 요약 내용과 오늘 신규 원문을 줄 것이다. 신규 원문이 과거 대비 기술적(양산성, 차별성)으로 새롭게 진보했거나 로드맵에 영향을 줄 '추가/변경된 핵심(Delta)'만 상품기획 관점에서 3줄(Bullet points)로 요약하라.
- 각 줄은 '- '로 시작하는 개조식 문장으로 작성하고 줄바꿈(\\n)으로 구분하라.
- 만약 과거 요약 컨텍스트가 없다면, 해당 신규 원문이 기존 업계 표준 대비 갖는 핵심 기술적 차별점과 상품기획 시사점을 3줄(Bullet points)로 요약하라.

[System Prompt 2: 이상 기류(Anomaly) 감지]
해당 기업의 최근 특허(R&D 의도) 데이터 흐름과 오늘 발생한 뉴스(설비투자/액션)를 비교하라. 양산 가속화 징후나 기술 노선 선회(Pivot) 등의 '이상 기류'가 감지되면 anomaly_flag를 true로, 감지 사유를 anomaly_description에 1줄로 작성하라.
- 당사(LG Display) 데이터인 경우 anomaly_flag는 원칙적으로 false로 두되, 당사의 중대한 신기술 돌파나 핵심 투자 발표인 경우 그 의의를 delta_summary에 강조하라.
- 이상 기류가 감지되지 않는 일반적인 동향이라면 anomaly_flag는 false, anomaly_description은 빈 문자열("")로 작성하라.

[출력 형식 (Strict JSON Only)]
다른 설명이나 마크다운 인사말 없이 반드시 아래 JSON 스키마로만 출력하라:
{
  "delta_summary": "- 첫 번째 핵심 변경점/차별점 요약\\n- 두 번째 양산성/성능 진보 요약\\n- 세 번째 상품기획/로드맵 영향 요약",
  "anomaly_flag": true,
  "anomaly_description": "이상 기류 감지 사유 1줄 요약 (anomaly_flag가 false면 빈 문자열)"
}"""

# 토큰 최적화를 위한 상한선 설정 (연관도 최상위 5건의 과거 요약본만 압축 주입 & 신규 본문 1,800자 제한으로 토큰 55% 절감)
MAX_PAST_CONTEXT_ITEMS: int = 5
MAX_NEW_CONTENT_CHARS: int = 1800


# =====================================================================
# 2. 타겟 레코드 조회 및 과거 30일 컨텍스트(Past Context) 빌더
# =====================================================================


def get_pending_delta_records(
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    `analysis_data` 테이블에서 `delta_summary`가 아직 NULL이거나 빈 문자열(`''`)인
    분석 대기 레코드를 `raw_data`와 조인하여 조회합니다.

    Args:
        limit (Optional[int]): 최대 조회 건수. None이면 전체 조회.
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        List[Dict[str, Any]]: 델타 분석 대기 레코드 리스트.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        sql = """
        SELECT
            a.id AS analysis_id,
            a.raw_id,
            a.tech_keywords,
            a.korean_translation,
            r.source_type,
            r.company,
            r.doc_id,
            r.title,
            r.abstract,
            r.content,
            r.published_date
        FROM analysis_data a
        INNER JOIN raw_data r ON a.raw_id = r.id
        WHERE a.delta_summary IS NULL OR TRIM(a.delta_summary) = ''
        ORDER BY r.published_date ASC, a.raw_id ASC
        """
        if limit is not None and int(limit) > 0:
            sql += f" LIMIT {int(limit)};"
        else:
            sql += ";"

        cursor.execute(sql)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except Exception as exc:
        logger.error(f"델타 분석 대기 레코드 조회 중 오류 발생: {exc}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()


def parse_keywords_list(raw_keywords: Any) -> List[str]:
    """
    `utils.db_helper.parse_and_normalize_keywords`를 재사용하여 키워드 리스트를 정규화 반환합니다.
    """
    return parse_and_normalize_keywords(raw_keywords)


def build_past_context(
    company: str,
    keywords: List[str],
    current_raw_id: int,
    reference_date: Optional[str] = None,
    days: int = 30,
    db_path: Optional[Path] = None,
) -> str:
    """
    대상 레코드의 기업명(`company`) 및 추출된 키워드(`tech_keywords`)를 기준으로,
    과거 30일간의 기존 요약본(`delta_summary`) 데이터만 DB에서 조회하여
    '과거 컨텍스트(Past Context)' 문자열로 조립합니다. (토큰 최적화를 위해 원문 `content`는 제외)

    특허(R&D 의도)와 뉴스(설비투자/액션) 간 교차 검증이 가능하도록 각 과거 요약의 `source_type`을 명시합니다.

    Args:
        company (str): 대상 기업명.
        keywords (List[str]): 대상 문서에서 추출된 동적 기술 키워드 리스트.
        current_raw_id (int): 현재 분석 중인 문서의 `raw_id` (자신은 컨텍스트에서 제외).
        reference_date (Optional[str]): 기준 일자 (YYYY-MM-DD). 미지정 시 당일 날짜.
        days (int): 과거 조회 기간 (기본값: 30일).
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        str: 조립된 과거 30일 요약 컨텍스트 문자열.
    """
    try:
        ref_dt = (
            datetime.strptime(reference_date[:10], "%Y-%m-%d")
            if reference_date and len(reference_date) >= 10
            else datetime.now()
        )
    except ValueError:
        ref_dt = datetime.now()

    start_date_str = (ref_dt - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date_str = ref_dt.strftime("%Y-%m-%d")

    conn = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        # 동일 기업(company)이거나 키워드가 겹칠 가능성이 있는 최근 30일 이내의 분석 완료 요약본 조회
        # (원문 content 컬럼은 SELECT하지 않아 메모리 및 토큰 원천 절약)
        query = """
        SELECT
            r.id AS raw_id,
            r.source_type,
            r.company,
            r.published_date,
            r.title,
            a.tech_keywords,
            a.delta_summary
        FROM analysis_data a
        INNER JOIN raw_data r ON a.raw_id = r.id
        WHERE r.id != ?
          AND a.delta_summary IS NOT NULL
          AND TRIM(a.delta_summary) != ''
          AND r.published_date >= ?
          AND r.published_date <= ?
        ORDER BY
          CASE WHEN r.company = ? THEN 0 ELSE 1 END ASC,
          r.published_date DESC,
          r.id DESC
        LIMIT 50;
        """
        cursor.execute(query, (current_raw_id, start_date_str, end_date_str, company))
        candidate_rows = cursor.fetchall()

        if not candidate_rows:
            return "최근 30일간 축적된 동일 기업/키워드 과거 요약 데이터가 없습니다."

        kw_lower_set = {k.lower() for k in keywords if k}
        scored_entries: List[tuple] = []

        for row in candidate_rows:
            row_company = row["company"]
            row_keywords = parse_keywords_list(row["tech_keywords"])
            row_kw_lower = {k.lower() for k in row_keywords}

            # 동일 기업 여부 및 키워드 중첩 개수를 점수화하여 가장 연관도 높은 상위 5건만 선별 (토큰 절약)
            is_same_company = row_company.upper() == company.upper()
            overlap_count = len(kw_lower_set & row_kw_lower)

            if not (is_same_company or overlap_count > 0):
                continue

            src_tag = "특허" if row["source_type"] == "patent" else "뉴스"
            pub_date = row["published_date"]
            kw_str = ", ".join(row_keywords) if row_keywords else "-"
            summary_clean = re.sub(r"\s+", " ", (row["delta_summary"] or "").strip())

            entry_text = (
                f"[{pub_date}|{row_company}|{src_tag}] {row['title']} ({kw_str}) -> {summary_clean}"
            )
            scored_entries.append(((1 if is_same_company else 0), overlap_count, entry_text))

        if not scored_entries:
            return "최근 30일간 해당 기업 또는 기술 키워드와 일치하는 과거 요약 데이터가 없습니다."

        scored_entries.sort(key=lambda x: (x[0], x[1]), reverse=True)
        top_entries = [item[2] for item in scored_entries[:MAX_PAST_CONTEXT_ITEMS]]
        return "\n".join(top_entries)

    except Exception as exc:
        logger.error(f"과거 컨텍스트 조회 중 오류 발생: {exc}", exc_info=True)
        return "과거 요약 컨텍스트 없음."
    finally:
        if conn:
            conn.close()


def build_delta_user_prompt(record: Dict[str, Any], past_context: str) -> str:
    """
    과거 30일 요약 컨텍스트(`past_context`)와 오늘의 신규 원문 데이터를 결합하여
    `gpt-4o`에 전달할 User Prompt를 생성합니다.
    (토큰 최적화: 불필요한 장식 기호를 제거하고 `content`가 `abstract` 폴백인 경우 중복 전송을 차단합니다.)
    """
    source_type = record.get("source_type", "patent")
    source_label = "특허(R&D/청구항)" if source_type == "patent" else "뉴스(투자/양산)"
    company = record.get("company", "Unknown")
    is_lgd = str(company).strip().upper() in ("LG DISPLAY", "LGD", "엘지디스플레이", "LG디스플레이")
    perspective_role = (
        "당사(자사, LGD) 자체 동향 — '당사(LGD)' 관점으로 서술"
        if is_lgd
        else "경쟁사/세트사 동향 — 당사(LGD) 관점의 위협·기회 시사점으로 서술"
    )
    published_date = record.get("published_date", "")
    title = (record.get("title") or "").strip()
    abstract = (record.get("abstract") or "").strip()
    raw_content = (record.get("content") or "").strip()
    is_redundant_fallback = raw_content.startswith(
        ("[독립항 미공개/요약 대체]", "[Independent Claim Fallback", "[원문 링크:")
    )
    content = "" if (is_redundant_fallback and abstract) else raw_content[:MAX_NEW_CONTENT_CHARS]
    keywords = parse_keywords_list(record.get("tech_keywords"))

    prompt_sections = [
        f"[1. 과거 30일 요약 컨텍스트 ({company} / {', '.join(keywords)})]",
        past_context,
        "",
        "[2. 신규 원문 데이터]",
        f"- 구분: {source_label} | 기업: {company} | 발행일: {published_date}",
        f"- 관점: {perspective_role}",
        f"- 키워드: {', '.join(keywords)}",
        f"- 제목: {title}",
    ]
    if abstract:
        prompt_sections.append(f"- 요약서: {abstract[:1000]}")
    if content and content != abstract:
        prompt_sections.append(f"- 핵심 본문/독립항:\n{content}")

    return "\n".join(prompt_sections)


# =====================================================================
# 3. 방어적 JSON 응답 파서 및 gpt-4o 호출 함수
# =====================================================================


def parse_delta_json_response(raw_llm_output: str) -> Dict[str, Any]:
    """
    `gpt-4o` 응답 문자열에서 `delta_summary`, `anomaly_flag`, `anomaly_description`을 포함한
    JSON 객체를 파싱 및 검증하여 반환합니다.

    Args:
        raw_llm_output (str): LLM 응답 원문 문자열.

    Returns:
        Dict[str, Any]: 정규화된 분석 결과 딕셔너리:
            - `delta_summary` (str): 3줄 요약 텍스트
            - `anomaly_flag` (bool): 이상 기류 여부
            - `anomaly_description` (str): 이상 기류 사유 1줄 요약

    Raises:
        json.JSONDecodeError: 유효한 JSON 객체를 파싱할 수 없거나 필수 필드가 비어 있을 때 발생.
    """
    if not raw_llm_output or not raw_llm_output.strip():
        raise json.JSONDecodeError("LLM 응답이 비어 있습니다.", raw_llm_output or "", 0)

    cleaned = raw_llm_output.strip()
    # 마크다운 코드블록(```json ... ```) 제거
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    parsed_obj: Any = None
    try:
        parsed_obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # 중괄호 `{ ... }` 구간만 정규식으로 추출하여 재시도
        brace_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if brace_match:
            parsed_obj = json.loads(brace_match.group(0))
        else:
            raise

    if not isinstance(parsed_obj, dict):
        raise json.JSONDecodeError("LLM 응답이 JSON 객체(Object) 형태가 아닙니다.", cleaned, 0)

    # 1. delta_summary 정규화 (리스트로 반환된 경우 줄바꿈 문자열로 결합)
    raw_delta = parsed_obj.get("delta_summary", "")
    if isinstance(raw_delta, list):
        delta_lines = []
        for line in raw_delta:
            line_str = str(line).strip()
            if line_str:
                if not line_str.startswith(("-", "•", "*")):
                    line_str = f"- {line_str}"
                delta_lines.append(line_str)
        delta_summary = "\n".join(delta_lines[:3])
    else:
        delta_summary = str(raw_delta or "").strip()

    if not delta_summary:
        raise json.JSONDecodeError("delta_summary 필드가 비어 있습니다.", cleaned, 0)

    # 2. anomaly_flag 정규화 (bool, 문자열 'true'/'false', 정수 1/0 모두 대응)
    raw_flag = parsed_obj.get("anomaly_flag", False)
    if isinstance(raw_flag, bool):
        anomaly_flag = raw_flag
    elif isinstance(raw_flag, str):
        anomaly_flag = raw_flag.strip().lower() in ("true", "1", "yes")
    else:
        anomaly_flag = bool(raw_flag)

    # 3. anomaly_description 정규화
    anomaly_description = str(parsed_obj.get("anomaly_description") or "").strip()
    if not anomaly_flag:
        anomaly_description = ""

    return {
        "delta_summary": delta_summary,
        "anomaly_flag": anomaly_flag,
        "anomaly_description": anomaly_description,
    }


def request_delta_analysis_from_llm(
    client: Any,
    user_prompt: str,
    model: str = DEFAULT_DELTA_MODEL,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """
    OpenAI `gpt-4o` 모델을 호출하여 델타 3줄 요약 및 이상 기류 탐지 결과를 JSON으로 반환받습니다.
    """
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_DELTA_AND_ANOMALY},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=600,
    )

    raw_output = ""
    if response.choices and response.choices[0].message:
        raw_output = response.choices[0].message.content or ""

    return parse_delta_json_response(raw_output)


# =====================================================================
# 3-2. [Phase 7 신규] 영문 원문 감지(Regex) 및 gpt-4o-mini 사전 한국어 번역
# =====================================================================

HANGUL_CHAR_REGEX = re.compile(r"[가-힣]")
LATIN_ALPHA_REGEX = re.compile(r"[A-Za-z]")


def is_english_text(text: Optional[str], title: Optional[str] = "") -> bool:
    """
    [Phase 7 신규] 정규표현식을 활용하여 수집된 원문(`content`) 및 제목(`title`)이
    영어인지 판별합니다.
    - 영문 알파벳(`[A-Za-z]`)이 충분히 존재하고 한글(`[가-힣]`) 비율이 전체 문자 대비 5% 미만이면
      영문 특허/문서로 판정합니다.
    """
    combined = f"{title or ''}\n{text or ''}".strip()
    if not combined:
        return False

    latin_count = len(LATIN_ALPHA_REGEX.findall(combined))
    hangul_count = len(HANGUL_CHAR_REGEX.findall(combined))

    if latin_count < 20:
        return False

    total_letters = latin_count + hangul_count
    hangul_ratio = hangul_count / max(total_letters, 1)
    return hangul_ratio < 0.05


def translate_english_patent_to_korean(
    client: Any,
    title: str,
    abstract: str,
    content: str,
    model: str = DEFAULT_TRANSLATION_MODEL,
    temperature: float = 0.15,
) -> str:
    """
    [Phase 7 신규] 영문 특허의 명칭(`title`), 요약서(`abstract`), 그리고 독립 청구항(`content`)을
    `gpt-4o-mini`에 전달하여 디스플레이 엔지니어들이 이해하기 쉬운 자연스러운 한국어 전문 용어로
    번역한 마크다운 텍스트를 반환합니다.
    (토큰 최적화: `content`가 `abstract` 폴백 복제본인 경우 중복 전송을 제거합니다.)
    """
    clean_abs = (abstract or "").strip()
    clean_body = (content or "").strip()
    if clean_body.startswith(("[Independent Claim Fallback", "[독립항 미공개/요약 대체]")) and clean_abs:
        clean_body = ""

    parts = [f"[Patent Title]:\n{(title or '').strip()}"]
    if clean_abs:
        parts.append(f"[Patent Abstract]:\n{clean_abs[:1000]}")
    if clean_body and clean_body != clean_abs:
        parts.append(f"[Independent Claims]:\n{clean_body[:MAX_NEW_CONTENT_CHARS]}")
    user_text = "\n\n".join(parts)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_ENGLISH_TRANSLATION},
            {"role": "user", "content": user_text},
        ],
        temperature=temperature,
        max_tokens=900,
    )

    if response.choices and response.choices[0].message:
        return (response.choices[0].message.content or "").strip()
    return ""


# =====================================================================
# 4. 오늘의 AI 종합 브리핑 (Executive Summary) 위임 함수 (중복 코드 제거)
# =====================================================================


def generate_and_store_executive_summary(
    target_date: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_DELTA_MODEL,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """
    중복 구현을 제거하고 `analyzers.executive_summary.generate_daily_executive_summary`로 단일 위임합니다.
    """
    from analyzers.executive_summary import generate_daily_executive_summary

    return generate_daily_executive_summary(
        target_date=target_date,
        api_key=api_key,
        model=model,
        db_path=db_path,
    )


# =====================================================================
# 5. 배치 실행 및 DB 업데이트 메인 파이프라인
# =====================================================================


def run_delta_analysis_batch(
    limit: Optional[int] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_DELTA_MODEL,
    db_path: Optional[Path] = None,
    run_exec_summary: bool = False,
) -> Dict[str, int]:
    """
    `analysis_data`에서 `delta_summary`가 비어 있는 레코드들을 조회하여,
    과거 30일 요약 컨텍스트와 함께 `gpt-4o`에 전달하고, 반환된 3줄 델타 요약 및 이상 기류 탐지 결과를
    `analysis_data` 테이블에 업데이트합니다.

    (토큰 최적화: 배치 스크립트 `run_pipeline.sh` 등에서 Step 5로 `executive_summary.py`를 별도 호출하므로
     `run_exec_summary` 기본값을 False로 두어 `gpt-4o` 이중 호출을 방지합니다.)
    """
    stats: Dict[str, int] = {
        "total_targets": 0,
        "succeeded": 0,
        "anomalies_detected": 0,
        "json_errors": 0,
        "api_errors": 0,
        "db_errors": 0,
    }

    if not HAS_OPENAI_SDK:
        logger.error("`openai` 패키지가 설치되어 있지 않습니다. `pip install openai` 후 실행해주세요.")
        return stats

    resolved_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    if not resolved_key or resolved_key == "your_openai_api_key_here":
        logger.error(
            "OPENAI_API_KEY가 설정되지 않았습니다. 프로젝트 루트의 `.env` 파일에 "
            "`OPENAI_API_KEY=sk-...`를 설정한 후 다시 실행해주세요."
        )
        return stats

    # 1. 델타 분석 대기 레코드 조회
    pending_records = get_pending_delta_records(limit=limit, db_path=db_path)
    stats["total_targets"] = len(pending_records)

    if not pending_records:
        logger.info("델타(Delta) 분석을 수행할 대기 레코드가 없습니다.")
        return stats

    logger.info(f"총 {len(pending_records)}건의 레코드에 대해 [{model}] 심층 델타 분석 및 이상 기류 탐지를 시작합니다.")
    client = OpenAI(api_key=resolved_key)

    # 2. 레코드별 순차 처리 (방금 분석 완료된 요약이 다음 레코드의 과거 컨텍스트로도 즉시 활용 가능)
    for record in pending_records:
        raw_id = record["raw_id"]
        doc_id = record.get("doc_id", "")
        company = record.get("company", "")
        published_date = record.get("published_date", "")
        keywords = parse_keywords_list(record.get("tech_keywords"))

        try:
            # 2-1. 과거 30일 요약 컨텍스트 조립 (전체 원문 대신 요약본 상위 5건만 로드)
            past_context = build_past_context(
                company=company,
                keywords=keywords,
                current_raw_id=raw_id,
                reference_date=published_date,
                days=30,
                db_path=db_path,
            )

            # 2-2. 프롬프트 조립 및 gpt-4o 호출
            user_prompt = build_delta_user_prompt(record, past_context)
            analysis_result = request_delta_analysis_from_llm(
                client=client,
                user_prompt=user_prompt,
                model=model,
            )

            delta_summary = analysis_result["delta_summary"]
            anomaly_flag = analysis_result["anomaly_flag"]
            anomaly_description = analysis_result["anomaly_description"]

            # 2-3. [Phase 7] 영문 특허/원문 판별 및 gpt-4o-mini 사전 한국어 번역 수행
            korean_translation = (record.get("korean_translation") or "").strip()
            raw_content = record.get("content") or ""
            raw_title = record.get("title") or ""
            raw_abstract = record.get("abstract") or ""

            if not korean_translation and is_english_text(raw_content, title=raw_title):
                try:
                    logger.info(
                        f"[raw_id={raw_id} | {doc_id}] 영문 원문 감지 -> [{DEFAULT_TRANSLATION_MODEL}] 한국어 전문 용어 번역 수행 중..."
                    )
                    korean_translation = translate_english_patent_to_korean(
                        client=client,
                        title=raw_title,
                        abstract=raw_abstract,
                        content=raw_content,
                        model=DEFAULT_TRANSLATION_MODEL,
                    )
                    if korean_translation:
                        stats["translated_count"] = stats.get("translated_count", 0) + 1
                except Exception as trans_err:
                    logger.warning(f"[raw_id={raw_id}] 영문 특허 번역 중 경고 (델타 분석은 정상 저장): {trans_err}")

            # 2-4. DB 업데이트 (`analysis_data` 테이블: delta_summary, anomaly, korean_translation 동시 반영)
            updated = update_analysis_delta(
                raw_id=raw_id,
                delta_summary=delta_summary,
                anomaly_flag=anomaly_flag,
                anomaly_description=anomaly_description,
                korean_translation=korean_translation,
                db_path=db_path,
            )

            if updated:
                stats["succeeded"] += 1
                if anomaly_flag:
                    stats["anomalies_detected"] += 1
                    logger.warning(
                        f"🚨 [이상 기류 감지! raw_id={raw_id} | {company} | {doc_id}] 사유: {anomaly_description}"
                    )
                else:
                    logger.info(f"[raw_id={raw_id} | {company} | {doc_id}] 델타 3줄 요약 업데이트 완료.")
            else:
                stats["db_errors"] += 1
                logger.error(f"[raw_id={raw_id}] 델타 분석 DB 업데이트 실패.")

            time.sleep(0.15)

        except json.JSONDecodeError as json_err:
            stats["json_errors"] += 1
            logger.error(
                f"[raw_id={raw_id} | doc_id={doc_id}] gpt-4o 응답 JSON 파싱 실패 (배치 계속 진행): {json_err}"
            )
            continue

        except OpenAIError as api_err:
            stats["api_errors"] += 1
            logger.error(
                f"[raw_id={raw_id} | doc_id={doc_id}] OpenAI API 호출 오류 발생 (배치 계속 진행): {api_err}"
            )
            continue

        except Exception as unexpected_err:
            stats["api_errors"] += 1
            logger.error(
                f"[raw_id={raw_id} | doc_id={doc_id}] 예기치 못한 오류 발생 (배치 계속 진행): {unexpected_err}",
                exc_info=True,
            )
            continue

    logger.info(
        f"델타 분석 및 이상 기류 탐지 배치 완료 | 대상: {stats['total_targets']}건, "
        f"성공: {stats['succeeded']}건 (이상 기류 감지: {stats['anomalies_detected']}건), "
        f"JSON오류: {stats['json_errors']}건, API오류: {stats['api_errors']}건, DB오류: {stats['db_errors']}건"
    )

    if run_exec_summary and stats["succeeded"] > 0:
        try:
            from analyzers.executive_summary import run_executive_summary_pipeline

            exec_res = run_executive_summary_pipeline(
                api_key=resolved_key,
                model=model,
                db_path=db_path,
            )
            stats["daily_summary_generated"] = 1 if exec_res.get("daily_generated") else 0
            stats["weekly_summary_generated"] = 1 if exec_res.get("weekly_generated") else 0
        except Exception as exec_err:
            logger.error(f"Executive Summary 파이프라인 후속 실행 중 오류: {exec_err}", exc_info=True)
            stats["daily_summary_generated"] = 0
            stats["weekly_summary_generated"] = 0

    return stats


if __name__ == "__main__":
    batch_stats = run_delta_analysis_batch()
    print(json.dumps(batch_stats, indent=2, ensure_ascii=False))
