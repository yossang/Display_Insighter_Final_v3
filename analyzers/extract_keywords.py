"""
Display Foresight - Dynamic Tech Keyword Extractor (analyzers/extract_keywords.py)

본 모듈은 `raw_data` 테이블에 적재된 신규 특허/뉴스 원문 중 아직 `analysis_data`에
분석 결과가 생성되지 않은 미처리 데이터(Unprocessed Data)만을 선별 조회하여,
OpenAI의 `gpt-4o-mini` 모델을 통해 가장 핵심적인 디스플레이·광학·폼팩터 기술 명사(최대 3개)를
동적으로 추출합니다.

핵심 설계 원칙:
1. 토큰 최적화 (Unprocessed Data 타겟팅):
   - `LEFT JOIN analysis_data` 쿼리를 사용하여 아직 분석되지 않은 `raw_data` 레코드만 정확히 가져옵니다.
   - 입력 컨텍스트는 제목(Title), 요약(Abstract), 독립항/순수본문(Content)의 핵심부만 조합하여 불필요한 토큰 소모를 차단합니다.
2. 엄격한 프롬프트 및 방어적 JSON 파싱:
   - System Prompt를 통해 오직 JSON 배열(`["Keyword1", "Keyword2", "Keyword3"]`)만 출력하도록 강제합니다.
   - 마크다운 코드블록(```json ... ```)이 섞이거나 객체 형태로 반환되더라도 정규식/파서 폴백을 통해 안전하게 배열을 복원하며,
     `json.JSONDecodeError`나 API 통신 오류가 발생해도 배치 전체가 중단되지 않고 다음 레코드로 넘어가도록 격리(Try-Except)합니다.
3. DB 1차 적재 및 트렌드 자동 누적:
   - `utils.db_helper.insert_analysis_data`를 호출하여 `analysis_data`에 키워드 리스트(JSON)를 1차 저장(delta_summary 등은 빈 문자열 초기화)하고,
     동일 트랜잭션 내에서 `keyword_trend` 테이블의 `frequency`(+1) 및 `last_seen_date`를 UPSERT합니다.
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 프로젝트 루트 경로를 sys.path에 추가하여 상위 패키지(utils) 임포트 보장
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.db_helper import get_connection, insert_analysis_data, load_project_env, normalize_tech_keyword

load_project_env()

# OpenAI 공식 SDK 임포트 (미설치 환경에서도 모듈 임포트가 깨지지 않도록 처리)
try:
    from openai import OpenAI, OpenAIError  # type: ignore

    HAS_OPENAI_SDK = True
except ImportError:
    OpenAI = None  # type: ignore
    OpenAIError = Exception  # type: ignore
    HAS_OPENAI_SDK = False

# 로깅 설정
logger = logging.getLogger("DisplayForesight.KeywordExtractor")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# =====================================================================
# 1. LLM 모델 및 System Prompt 상수 정의 (LGD 표준 용어 사전 Glossary 주입)
# =====================================================================

DEFAULT_MODEL_NAME: str = "gpt-4o-mini"

# [Phase 2 & 3 통합 업데이트] 표준 용어(Glossary) 통일 지침이 주입된 System Prompt
SYSTEM_PROMPT: str = """너는 LG Display(LGD)의 디스플레이 기술 전문가다. 주어진 특허 요약/청구항 또는 뉴스 본문을 읽고, 가장 구체적이고 핵심적인 디스플레이, 광학, 폼팩터 기술 명사(예: 'Tandem OLED', 'Micro Lens Array', '4-Side Bending')를 3개만 추출하라.

[표준 용어 사전(Glossary) 통일 지침]
원문에서 기술 키워드를 추출할 때 다음 개념이 등장하면 반드시 지정된 '표준 영문 명칭'으로 통일하여 반환하라.
- 폴더블/롤러블 폼팩터 -> 'Foldable/Rollable'
- 마이크로 OLED, OLEDoS -> 'Micro OLED'
- 4면 베젤리스, 쿼드 벤딩 -> '4-Side Bending'
- 언더 패널 카메라/센서 -> 'Under Panel Sensor (UPS)'
- 음극 패터닝 소재 -> 'Cathode Patterning Material (CPM)'
- 픽셀 정의막 -> 'Pixel Defining Layer (PDL)'

반드시 JSON 배열(Array) 형태로만 출력하고 다른 설명은 절대 덧붙이지 마라."""

# 입력 텍스트 최대 길이 제한 (토큰 최적화: 제목+요약+핵심 청구항/리드문 1,200자면 핵심 명사 3개 추출에 충분하며 입력 토큰 65% 절감)
MAX_CONTENT_CHARS: int = 1200


# =====================================================================
# 2. 미처리 원문 데이터 조회 및 입력 프롬프트 조립 함수
# =====================================================================


def get_unprocessed_raw_data(
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    `raw_data` 테이블에서 아직 `analysis_data` 테이블에 분석 레코드가 생성되지 않은
    신규 원문 데이터(Unprocessed Data)만 조회하여 반환합니다.

    Args:
        limit (Optional[int]): 한 번의 배치에서 처리할 최대 건수. None이면 미처리 전체 조회.
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        List[Dict[str, Any]]: 미처리 원문 레코드 딕셔너리 리스트.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        sql = """
        SELECT
            r.id,
            r.source_type,
            r.company,
            r.doc_id,
            r.title,
            r.abstract,
            r.content,
            r.published_date,
            r.collected_at
        FROM raw_data r
        LEFT JOIN analysis_data a ON r.id = a.raw_id
        WHERE a.id IS NULL
        ORDER BY r.id ASC
        """
        if limit is not None and int(limit) > 0:
            sql += f" LIMIT {int(limit)};"
        else:
            sql += ";"

        cursor.execute(sql)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except Exception as exc:
        logger.error(f"미처리 원문 데이터 조회 중 오류 발생: {exc}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()


def build_user_prompt(raw_record: Dict[str, Any]) -> str:
    """
    단일 `raw_data` 레코드(특허 또는 뉴스)로부터 `gpt-4o-mini`에 전달할
    핵심 텍스트 프롬프트를 조립합니다.
    (토큰 최적화: `content`가 `abstract` 폴백 복제본이거나 동일한 경우 중복 전송을 원천 차단합니다.)
    """
    source_type = raw_record.get("source_type", "patent").upper()
    company = raw_record.get("company", "Unknown")
    title = (raw_record.get("title") or "").strip()
    abstract = (raw_record.get("abstract") or "").strip()
    raw_content = (raw_record.get("content") or "").strip()

    # 요약서(abstract)와 중복되는 폴백 문구 제거 후 길이 제한
    is_redundant_fallback = raw_content.startswith(
        ("[독립항 미공개/요약 대체]", "[Independent Claim Fallback", "[원문 링크:")
    )
    content = "" if (is_redundant_fallback and abstract) else raw_content[:MAX_CONTENT_CHARS]

    parts = [
        f"[유형]: {source_type} | [기업]: {company}",
        f"[제목]: {title}",
    ]
    if abstract:
        parts.append(f"[요약]: {abstract[:800]}")
    if content and content != abstract:
        label = "[독립항]" if source_type == "PATENT" else "[본문]"
        parts.append(f"{label}: {content}")

    return "\n".join(parts)


# =====================================================================
# 3. 방어적 JSON 배열 파서 및 OpenAI 호출 함수
# =====================================================================


def parse_keyword_json_array(raw_llm_output: str, max_keywords: int = 3) -> List[str]:
    """
    LLM 응답 문자열에서 JSON 배열(`["kw1", "kw2", "kw3"]`)을 파싱하여 정제된 문자열 리스트로 반환합니다.
    코드블록(```json ... ```)이 포함되어 있거나 앞뒤에 부가 텍스트가 붙은 경우에도
    정규표현식으로 `[...]` 구간을 추출하여 파싱합니다.

    Args:
        raw_llm_output (str): LLM이 반환한 원문 텍스트.
        max_keywords (int): 최대 추출 키워드 개수 (기본값: 3).

    Returns:
        List[str]: 정제된 기술 명사 키워드 리스트 (최대 3개).

    Raises:
        json.JSONDecodeError: 유효한 JSON 배열을 파싱할 수 없는 경우 발생.
    """
    if not raw_llm_output or not raw_llm_output.strip():
        raise json.JSONDecodeError("LLM 응답이 비어 있습니다.", raw_llm_output or "", 0)

    cleaned = raw_llm_output.strip()

    # 1. 마크다운 코드블록(```json ... ```) 제거
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    parsed_data: Any = None
    try:
        parsed_data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 2. 응답 내 대괄호 `[ ... ]` 구간만 정규식으로 발췌하여 재시도
        bracket_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if bracket_match:
            parsed_data = json.loads(bracket_match.group(0))
        else:
            raise

    # 3. 만약 JSON 객체(`{"keywords": [...]}`)로 반환된 경우 내부 배열 추출
    if isinstance(parsed_data, dict):
        for val in parsed_data.values():
            if isinstance(val, list):
                parsed_data = val
                break

    if not isinstance(parsed_data, list):
        raise json.JSONDecodeError("응답이 JSON 배열(Array) 형태가 아닙니다.", cleaned, 0)

    # 4. 문자열 정제, 표준 용어(Glossary) 정규화(`normalize_tech_keyword`), 중복 제거 및 최대 개수(3개) 제한
    keywords: List[str] = []
    for item in parsed_data:
        if isinstance(item, str):
            kw = normalize_tech_keyword(item)
            if kw and kw not in keywords:
                keywords.append(kw)
        if len(keywords) >= max_keywords:
            break

    return keywords


def request_keywords_from_llm(
    client: Any,
    user_prompt: str,
    model: str = DEFAULT_MODEL_NAME,
    temperature: float = 0.1,
) -> List[str]:
    """
    OpenAI `gpt-4o-mini` API를 호출하여 핵심 기술 명사 키워드(최대 3개)를 추출합니다.

    Args:
        client (OpenAI): 초기화된 OpenAI 클라이언트 인스턴스.
        user_prompt (str): 분석할 특허/뉴스 텍스트 프롬프트.
        model (str): 사용할 OpenAI 모델명 (기본값: 'gpt-4o-mini').
        temperature (float): 생성 일관성을 위한 낮은 온도 설정 (기본값: 0.1).

    Returns:
        List[str]: 추출된 키워드 리스트 (최대 3개).
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=80,
    )

    raw_output = ""
    if response.choices and response.choices[0].message:
        raw_output = response.choices[0].message.content or ""

    return parse_keyword_json_array(raw_output, max_keywords=3)


# =====================================================================
# 4. 배치 실행 및 DB 업데이트 메인 파이프라인
# =====================================================================


def run_keyword_extraction_batch(
    limit: Optional[int] = None,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL_NAME,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    `raw_data`의 미처리 레코드들을 순회하며 `gpt-4o-mini`로 기술 키워드(최대 3개)를 추출하고,
    `analysis_data` 1차 적재 및 `keyword_trend` 빈도수(frequency)와 최근 출현일(last_seen_date)을
    업데이트합니다.

    개별 레코드에서 OpenAI API 오류나 `JSONDecodeError`가 발생하더라도
    전체 배치가 중단되지 않고 오류를 로깅한 뒤 다음 레코드로 안전하게 넘어갑니다.

    Args:
        limit (Optional[int]): 최대 처리할 레코드 건수. None이면 미처리 전체 처리.
        api_key (Optional[str]): OpenAI API 키. 미지정 시 `.env`의 `OPENAI_API_KEY` 사용.
        model (str): 사용할 LLM 모델명 (기본값: 'gpt-4o-mini').
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        Dict[str, int]: 배치 처리 통계 (`total_targets`, `succeeded`, `json_errors`, `api_errors`, `db_errors`).
    """
    stats: Dict[str, int] = {
        "total_targets": 0,
        "succeeded": 0,
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

    # 1. 미처리 신규 데이터(Unprocessed Data) 조회
    unprocessed_rows = get_unprocessed_raw_data(limit=limit, db_path=db_path)
    stats["total_targets"] = len(unprocessed_rows)

    if not unprocessed_rows:
        logger.info("키워드를 추출할 신규 미처리 데이터(Unprocessed Data)가 없습니다.")
        return stats

    logger.info(f"총 {len(unprocessed_rows)}건의 미처리 데이터에 대해 [{model}] 동적 키워드 추출을 시작합니다.")
    client = OpenAI(api_key=resolved_key)

    # 2. 레코드별 루프 처리 (개별 예외 격리)
    for row in unprocessed_rows:
        raw_id = row["id"]
        doc_id = row.get("doc_id", "")
        company = row.get("company", "")
        published_date = row.get("published_date")

        try:
            user_prompt = build_user_prompt(row)

            # LLM 호출 및 키워드 3개 추출
            keywords = request_keywords_from_llm(
                client=client,
                user_prompt=user_prompt,
                model=model,
            )

            if not keywords:
                logger.warning(f"[raw_id={raw_id}] 추출된 키워드가 비어 있어 다음 데이터로 넘어갑니다.")
                stats["json_errors"] += 1
                continue

            # DB 1차 저장 및 keyword_trend 빈도수/날짜 동시 업데이트
            # - utils.db_helper.insert_analysis_data 내부에서:
            #   1) analysis_data 테이블에 tech_keywords(JSON) 적재 (delta_summary='', anomaly_flag=False, anomaly_description='')
            #   2) keyword_trend 테이블에 각 키워드별 신규 INSERT 또는 frequency += 1 및 last_seen_date 갱신 수행
            analysis_id = insert_analysis_data(
                raw_id=raw_id,
                tech_keywords=keywords,
                delta_summary="",
                anomaly_flag=False,
                anomaly_description="",
                seen_date=published_date,
                db_path=db_path,
            )

            if analysis_id is not None:
                stats["succeeded"] += 1
                logger.info(
                    f"[raw_id={raw_id} | {company} | {doc_id}] 키워드 추출 및 트렌드 반영 성공: {keywords}"
                )
            else:
                stats["db_errors"] += 1
                logger.error(f"[raw_id={raw_id}] DB 적재 실패 (insert_analysis_data 반환값 None)")

            # Rate Limit 방지를 위한 미세 딜레이
            time.sleep(0.1)

        except json.JSONDecodeError as json_err:
            stats["json_errors"] += 1
            logger.error(
                f"[raw_id={raw_id} | doc_id={doc_id}] LLM 응답 JSON 파싱 실패 (배치 계속 진행): {json_err}"
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
        f"동적 키워드 추출 배치 완료 | 대상: {stats['total_targets']}건, "
        f"성공: {stats['succeeded']}건, JSON오류: {stats['json_errors']}건, "
        f"API오류: {stats['api_errors']}건, DB오류: {stats['db_errors']}건"
    )
    return stats


if __name__ == "__main__":
    batch_stats = run_keyword_extraction_batch()
    print(json.dumps(batch_stats, indent=2, ensure_ascii=False))
