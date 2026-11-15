"""
Display Foresight - Database Helper Module (utils/db_helper.py)

본 모듈은 SQLite3 기반 `display_foresight.db`와의 커넥션 관리, 중복 문서 체크,
특허/뉴스 원문(`raw_data`, `source_url` 포함) 적재, LLM 심층 분석(`analysis_data`) 적재 및
동적 키워드 빈도(`keyword_trend`) 자동 누적(UPSERT), 일별 AI 종합 브리핑(`daily_summary`) 저장/조회,
그리고 대시보드/엑셀 추출용 통합 데이터 조회 기능을 제공합니다.
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

# 로깅 설정
logger = logging.getLogger("DisplayForesight.DBHelper")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# 프로젝트 루트 및 기본 DB 경로 계산 (utils/ 상위 폴더가 프로젝트 루트)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH: Path = PROJECT_ROOT / "display_foresight.db"


def load_project_env() -> None:
    """
    프로젝트 루트의 `.env` 환경변수를 로드합니다 (코드 중복 방지를 위한 공통 헬퍼).
    `python-dotenv` 설치 시 우선 활용하며, 미설치 환경에서는 경량 파서로 폴백합니다.
    """
    env_file = PROJECT_ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file)
    except ImportError:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, v = stripped.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def get_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    SQLite3 데이터베이스 연결(Connection) 객체를 생성하여 반환합니다.
    컬럼명 기반 딕셔너리 접근이 가능하도록 `sqlite3.Row` 팩토리를 설정하며,
    외래키 제약조건(Foreign Keys)과 WAL 모드를 활성화합니다.
    """
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    try:
        conn = sqlite3.connect(str(target_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn
    except sqlite3.Error as db_err:
        logger.error(f"DB 커넥션 생성 실패 (경로: {target_path}): {db_err}", exc_info=True)
        raise


def get_existing_doc_ids(db_path: Optional[Union[str, Path]] = None) -> Set[str]:
    """
    배치 수집 시작 시 `raw_data`의 모든 `doc_id` 집합을 단 1회 쿼리로 메모리에 로드하여
    루프 내 반복적인 DB 커넥션 생성을 제거하고 O(1) 해시 중복 검사를 지원합니다.
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT doc_id FROM raw_data;")
        return {str(row[0]).strip() for row in cursor.fetchall() if row[0]}
    except sqlite3.Error:
        return set()
    finally:
        if conn:
            conn.close()


def is_duplicate_doc(doc_id: str, db_path: Optional[Union[str, Path]] = None) -> bool:
    """
    특허번호 또는 기사 URL 해시(`doc_id`)가 이미 `raw_data` 테이블에 존재하는지 검사합니다.
    """
    if not doc_id or not str(doc_id).strip():
        logger.warning("is_duplicate_doc 호출 시 빈 doc_id가 전달되었습니다.")
        return False

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM raw_data WHERE doc_id = ? LIMIT 1;",
            (str(doc_id).strip(),),
        )
        row = cursor.fetchone()
        return row is not None
    except sqlite3.Error as db_err:
        logger.error(f"중복 문서 확인 중 DB 오류 발생 (doc_id={doc_id}): {db_err}", exc_info=True)
        return False
    except Exception as exc:
        logger.error(f"중복 문서 확인 중 예기치 못한 오류 발생 (doc_id={doc_id}): {exc}", exc_info=True)
        return False
    finally:
        if conn:
            conn.close()


def insert_raw_data(
    source_type: str,
    company: str,
    doc_id: str,
    title: str,
    abstract: Optional[str] = "",
    content: Optional[str] = "",
    published_date: Optional[str] = None,
    collected_at: Optional[str] = None,
    source_url: Optional[str] = "",
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[int]:
    """
    수집된 특허 또는 뉴스 원문 데이터를 `raw_data` 테이블에 단일 커넥션(`INSERT OR IGNORE`)으로 적재합니다.
    (불필요한 사전 `is_duplicate_doc` 이중 커넥션 호출을 제거하여 I/O를 50% 절감합니다.)
    """
    normalized_source = source_type.strip().lower()
    if normalized_source not in ("patent", "news"):
        logger.error(f"유효하지 않은 source_type 입니다: '{source_type}' ('patent' 또는 'news'만 허용)")
        return None

    clean_doc_id = str(doc_id).strip()
    if not clean_doc_id or not title:
        logger.error("필수 파라미터(doc_id, title)가 누락되었습니다.")
        return None

    now_dt = datetime.now()
    pub_date_str = published_date.strip() if published_date else now_dt.strftime("%Y-%m-%d")
    coll_at_str = collected_at.strip() if collected_at else now_dt.strftime("%Y-%m-%d %H:%M:%S")
    clean_url = (source_url or "").strip()

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        insert_sql = """
        INSERT OR IGNORE INTO raw_data (
            source_type, company, doc_id, title, abstract, content, source_url, published_date, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        cursor.execute(
            insert_sql,
            (
                normalized_source,
                company.strip(),
                clean_doc_id,
                title.strip(),
                (abstract or "").strip(),
                (content or "").strip(),
                clean_url,
                pub_date_str,
                coll_at_str,
            ),
        )
        conn.commit()
        if cursor.rowcount == 0:
            logger.info(f"이미 수집된 문서입니다 (중복 스킵): doc_id={clean_doc_id}")
            return None
        inserted_id = cursor.lastrowid
        logger.info(f"원문 데이터 적재 완료: id={inserted_id}, source={normalized_source}, doc_id={clean_doc_id}")
        return inserted_id

    except sqlite3.IntegrityError as int_err:
        logger.warning(f"원문 적재 중 무결성 제약 위반 (중복 doc_id={clean_doc_id}): {int_err}")
        if conn:
            conn.rollback()
        return None
    except sqlite3.Error as db_err:
        logger.error(f"원문 데이터 적재 중 DB 오류 발생 (doc_id={clean_doc_id}): {db_err}", exc_info=True)
        if conn:
            conn.rollback()
        return None
    except Exception as exc:
        logger.error(f"원문 데이터 적재 중 예기치 못한 오류 발생: {exc}", exc_info=True)
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


# =====================================================================
# [Phase 2 & 3 통합 업데이트] 표준 기술 용어(Glossary) 정규화 매핑 및 전처리 함수
# =====================================================================

# 대소문자, 하이픈(-), 국문/약어 변형을 LGD 표준 영문 명칭으로 통일하는 정규화 규칙
STANDARD_GLOSSARY_PATTERNS: List[ tuple ] = [
    # 1. 폴더블/롤러블 폼팩터 -> 'Foldable/Rollable'
    (
        re.compile(
            r"^(?:foldable\s*[/&\-\s]\s*rollable|foldable|rollable|slidable|flexible\s+oled|폴더블|롤러블|슬라이더블|폴더블\s*[/·]\s*롤러블)(?:\s+oled|\s+display|\s+form\s*factor)?$",
            re.IGNORECASE,
        ),
        "Foldable/Rollable",
    ),
    # 2. 마이크로 OLED, OLEDoS -> 'Micro OLED'
    (
        re.compile(
            r"^(?:micro[\s\-]*oled|oledos|oled[\s\-]*on[\s\-]*silicon|마이크로\s*oled|마이크로\s*올레드|올레도스)$",
            re.IGNORECASE,
        ),
        "Micro OLED",
    ),
    # 3. 4면 베젤리스, 쿼드 벤딩 -> '4-Side Bending'
    (
        re.compile(
            r"^(?:4[\s\-]*side(?:d)?\s*bending|four[\s\-]*side(?:d)?\s*bending|quad[\s\-]*bending|quad[\s\-]*curved|4면\s*벤딩|4면\s*베젤리스|쿼드\s*벤딩)$",
            re.IGNORECASE,
        ),
        "4-Side Bending",
    ),
    # 4. 언더 패널 카메라/센서 -> 'Under Panel Sensor (UPS)'
    (
        re.compile(
            r"^(?:under[\s\-]*panel[\s\-]*(?:sensor|camera)(?:\s*\(ups\))?|under[\s\-]*display[\s\-]*(?:camera|sensor)(?:\s*\(udc\))?|ups|udc|언더\s*패널\s*센서|언더\s*패널\s*카메라|언더\s*디스플레이\s*카메라)$",
            re.IGNORECASE,
        ),
        "Under Panel Sensor (UPS)",
    ),
    # 5. 음극 패터닝 소재 -> 'Cathode Patterning Material (CPM)'
    (
        re.compile(
            r"^(?:cathode[\s\-]*patterning[\s\-]*material(?:\s*\(cpm\))?|cpm|음극\s*패터닝\s*소재|캐소드\s*패터닝(?:\s*소재)?)$",
            re.IGNORECASE,
        ),
        "Cathode Patterning Material (CPM)",
    ),
    # 6. 픽셀 정의막 -> 'Pixel Defining Layer (PDL)'
    (
        re.compile(
            r"^(?:pixel[\s\-]*defining[\s\-]*layer(?:\s*\(pdl\))?|pdl|픽셀\s*정의막|화소\s*정의막)$",
            re.IGNORECASE,
        ),
        "Pixel Defining Layer (PDL)",
    ),
    # 기타 주요 차세대 기술 키워드 하이픈/대소문자 표준화
    (
        re.compile(r"^(?:two[\s\-]*stack\s+tandem|tandem[\s\-]*oled|tandem|투스택\s*탠덤|탠덤\s*oled)$", re.IGNORECASE),
        "Tandem OLED",
    ),
    (
        re.compile(r"^(?:micro[\s\-]*lens[\s\-]*array(?:\s*\(mla\))?|mla|마이크로\s*렌즈\s*어레이)$", re.IGNORECASE),
        "Micro Lens Array",
    ),
    (
        re.compile(r"^(?:hybrid[\s\-]*bonding|하이브리드\s*본딩)$", re.IGNORECASE),
        "Hybrid Bonding",
    ),
    (
        re.compile(r"^(?:privacy[\s\-]*mode|switchable[\s\-]*privacy|시야각\s*제어|프라이버시\s*모드)$", re.IGNORECASE),
        "Privacy Mode",
    ),
    (
        re.compile(r"^(?:micro[\s\-]*led|μled|마이크로\s*led)$", re.IGNORECASE),
        "Micro LED",
    ),
    (
        re.compile(r"^(?:ltpo(?:\s*tft|\s*backplane)?)$", re.IGNORECASE),
        "LTPO TFT",
    ),
]


def normalize_tech_keyword(raw_keyword: str) -> str:
    """
    [Phase 2 & 3 통합 업데이트] DB Insert/Select 시 기술 키워드의 대소문자, 하이픈(`-`),
    국문/영문 이표기를 LGD 표준 영문 용어(Glossary)로 일관되게 치환·정규화합니다.
    예: 'micro-oled' / 'OLEDoS' -> 'Micro OLED', 'udc' -> 'Under Panel Sensor (UPS)'
    """
    cleaned = re.sub(r"\s+", " ", str(raw_keyword or "")).strip().strip("'\"#")
    if not cleaned:
        return ""

    for pattern, standard_term in STANDARD_GLOSSARY_PATTERNS:
        if pattern.match(cleaned):
            return standard_term

    # 일반 하이픈 연결 단어 정리 (예: 'hybrid-bonding' -> 'Hybrid Bonding', 단 '4-Side', 'QD-OLED', '8.6G' 등은 보존)
    lower_val = cleaned.lower()
    if lower_val == "micro-oled":
        return "Micro OLED"
    if lower_val in ("foldable", "rollable"):
        return "Foldable/Rollable"

    return cleaned


def parse_and_normalize_keywords(raw_keywords: Any, max_keywords: Optional[int] = None) -> List[str]:
    """
    리스트, JSON 배열 문자열, 또는 콤마 구분 문자열 형태의 `raw_keywords`를 파싱하고,
    `normalize_tech_keyword()`를 거쳐 중복이 제거된 표준 키워드 리스트를 반환합니다.
    (여러 모듈에 산재하던 키워드 파싱·정규화 로직을 단일 함수로 통합)
    """
    raw_list: List[str] = []
    if isinstance(raw_keywords, list):
        raw_list = [str(k).strip() for k in raw_keywords if str(k).strip()]
    elif isinstance(raw_keywords, str):
        s = raw_keywords.strip()
        if s:
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    raw_list = [str(k).strip() for k in parsed if str(k).strip()]
                else:
                    raw_list = [str(parsed).strip()]
            except json.JSONDecodeError:
                raw_list = [k.strip() for k in s.split(",") if k.strip()]

    normalized: List[str] = []
    for item in raw_list:
        norm = normalize_tech_keyword(item)
        if norm and norm not in normalized:
            normalized.append(norm)
            if max_keywords and len(normalized) >= max_keywords:
                break
    return normalized


def insert_analysis_data(
    raw_id: int,
    tech_keywords: Union[List[str], str],
    delta_summary: str,
    anomaly_flag: bool = False,
    anomaly_description: Optional[str] = "",
    korean_translation: Optional[str] = "",
    analyzed_at: Optional[str] = None,
    seen_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[int]:
    """
    LLM 분석 결과를 `analysis_data` 테이블에 적재하고, 추출된 동적 키워드 리스트를 파싱하여
    `keyword_trend` 테이블의 출현 빈도(`frequency`) 및 최근 출현일(`last_seen_date`)을
    단일 트랜잭션으로 업데이트합니다.
    (동일 `raw_id` 재적재 시 `keyword_trend.frequency`가 중복 증가하지 않도록 방어합니다.)
    """
    now_dt = datetime.now()
    analyzed_at_str = analyzed_at.strip() if analyzed_at else now_dt.strftime("%Y-%m-%d %H:%M:%S")

    unique_keywords: List[str] = parse_and_normalize_keywords(tech_keywords)
    keywords_json_str: str = json.dumps(unique_keywords, ensure_ascii=False)

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT published_date FROM raw_data WHERE id = ?;", (raw_id,))
        raw_row = cursor.fetchone()
        if not raw_row:
            logger.error(f"존재하지 않는 raw_id 입니다: raw_id={raw_id}")
            return None

        # 이미 analysis_data에 존재하는 raw_id인지 확인 (재실행 시 keyword_trend 중복 카운트 방지)
        cursor.execute("SELECT id FROM analysis_data WHERE raw_id = ? LIMIT 1;", (raw_id,))
        existing_analysis = cursor.fetchone()
        is_new_analysis = existing_analysis is None

        effective_seen_date = (
            seen_date.strip()
            if seen_date
            else (raw_row["published_date"][:10] if raw_row["published_date"] else now_dt.strftime("%Y-%m-%d"))
        )

        insert_analysis_sql = """
        INSERT INTO analysis_data (
            raw_id, tech_keywords, delta_summary, anomaly_flag, anomaly_description, korean_translation, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_id) DO UPDATE SET
            tech_keywords = excluded.tech_keywords,
            delta_summary = excluded.delta_summary,
            anomaly_flag = excluded.anomaly_flag,
            anomaly_description = excluded.anomaly_description,
            korean_translation = CASE
                WHEN excluded.korean_translation != '' THEN excluded.korean_translation
                ELSE analysis_data.korean_translation
            END,
            analyzed_at = excluded.analyzed_at;
        """
        cursor.execute(
            insert_analysis_sql,
            (
                raw_id,
                keywords_json_str,
                (delta_summary or "").strip(),
                1 if anomaly_flag else 0,
                (anomaly_description or "").strip(),
                (korean_translation or "").strip(),
                analyzed_at_str,
            ),
        )
        analysis_id = cursor.lastrowid or (existing_analysis["id"] if existing_analysis else None)

        if is_new_analysis:
            upsert_keyword_sql = """
            INSERT INTO keyword_trend (keyword, frequency, last_seen_date)
            VALUES (?, 1, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                frequency = keyword_trend.frequency + 1,
                last_seen_date = CASE
                    WHEN excluded.last_seen_date > keyword_trend.last_seen_date THEN excluded.last_seen_date
                    ELSE keyword_trend.last_seen_date
                END;
            """
            for kw in unique_keywords:
                cursor.execute(upsert_keyword_sql, (kw, effective_seen_date))

        conn.commit()
        logger.info(
            f"분석 데이터 적재 및 키워드 트렌드({len(unique_keywords)}건) 업데이트 완료: "
            f"raw_id={raw_id}, anomaly={bool(anomaly_flag)}"
        )
        return analysis_id

    except sqlite3.Error as db_err:
        logger.error(f"분석 데이터 적재 중 DB 오류 발생 (raw_id={raw_id}): {db_err}", exc_info=True)
        if conn:
            conn.rollback()
        return None
    except Exception as exc:
        logger.error(f"분석 데이터 적재 중 예기치 못한 오류 발생 (raw_id={raw_id}): {exc}", exc_info=True)
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def update_analysis_delta(
    raw_id: int,
    delta_summary: str,
    anomaly_flag: bool = False,
    anomaly_description: Optional[str] = "",
    korean_translation: Optional[str] = None,
    analyzed_at: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    Phase 3-1에서 1차 적재된 `analysis_data` 레코드에 대해, Phase 3-2 / Phase 7의 심층 분석 결과
    (`delta_summary`, `anomaly_flag`, `anomaly_description`, `korean_translation`, `analyzed_at`)를 업데이트합니다.
    """
    now_str = analyzed_at.strip() if analyzed_at else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn: Optional[sqlite3.Connection] = None

    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        if korean_translation is not None:
            update_sql = """
            UPDATE analysis_data
            SET delta_summary = ?,
                anomaly_flag = ?,
                anomaly_description = ?,
                korean_translation = ?,
                analyzed_at = ?
            WHERE raw_id = ?;
            """
            params = (
                (delta_summary or "").strip(),
                1 if anomaly_flag else 0,
                (anomaly_description or "").strip(),
                korean_translation.strip(),
                now_str,
                raw_id,
            )
        else:
            update_sql = """
            UPDATE analysis_data
            SET delta_summary = ?,
                anomaly_flag = ?,
                anomaly_description = ?,
                analyzed_at = ?
            WHERE raw_id = ?;
            """
            params = (
                (delta_summary or "").strip(),
                1 if anomaly_flag else 0,
                (anomaly_description or "").strip(),
                now_str,
                raw_id,
            )

        cursor.execute(update_sql, params)
        conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(
                f"델타 분석/번역 업데이트 완료: raw_id={raw_id}, anomaly={bool(anomaly_flag)}, "
                f"translated={bool(korean_translation)}"
            )
        else:
            logger.warning(f"업데이트 대상 analysis_data 레코드가 존재하지 않습니다: raw_id={raw_id}")
        return updated

    except sqlite3.Error as db_err:
        logger.error(f"델타 분석 결과 업데이트 중 DB 오류 발생 (raw_id={raw_id}): {db_err}", exc_info=True)
        if conn:
            conn.rollback()
        return False
    except Exception as exc:
        logger.error(f"델타 분석 결과 업데이트 중 예기치 못한 오류 발생 (raw_id={raw_id}): {exc}", exc_info=True)
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def update_korean_translation(
    raw_id: int,
    korean_translation: str,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    [Phase 7 신규] 특정 `raw_id`에 해당하는 `analysis_data.korean_translation` 컬럼만 단독 업데이트합니다.
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE analysis_data SET korean_translation = ? WHERE raw_id = ?;",
            ((korean_translation or "").strip(), raw_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as db_err:
        logger.error(f"국문 번역 업데이트 중 DB 오류 발생 (raw_id={raw_id}): {db_err}", exc_info=True)
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def upsert_executive_summary(
    date: str,
    summary_type: str,
    content: str,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    [Phase 6-2 신규] 특정 날짜(`YYYY-MM-DD`) 및 유형(`'daily'` 또는 `'weekly'`)의
    AI 종합 요약 마크다운 본문(`content`)을 `executive_summary` 테이블에 저장(UPSERT)합니다.

    Args:
        date (str): 기준 날짜 문자열 (YYYY-MM-DD).
        summary_type (str): 요약 구분 ('daily' 또는 'weekly').
        content (str): 마크다운 형식의 종합 브리핑 본문.
        db_path (Optional[Union[str, Path]]): 사용할 DB 경로.

    Returns:
        bool: 저장 성공 여부 (True/False).
    """
    clean_date = (date or "").strip()[:10]
    clean_type = (summary_type or "daily").strip().lower()
    clean_content = (content or "").strip()

    if clean_type not in ("daily", "weekly"):
        logger.error(f"유효하지 않은 summary_type 입니다: {summary_type} ('daily' 또는 'weekly'만 허용)")
        return False

    if not clean_date or not clean_content:
        logger.error("upsert_executive_summary 호출 시 date 또는 content가 비어 있습니다.")
        return False

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS executive_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                summary_type TEXT NOT NULL CHECK(summary_type IN ('daily', 'weekly')),
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(date, summary_type)
            );
            """
        )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
        INSERT INTO executive_summary (date, summary_type, content, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, summary_type) DO UPDATE SET
            content = excluded.content,
            updated_at = excluded.updated_at;
        """
        cursor.execute(sql, (clean_date, clean_type, clean_content, now_str))
        conn.commit()
        logger.info(f"executive_summary 저장 완료: type={clean_type}, date={clean_date}")
        return True

    except sqlite3.Error as db_err:
        logger.error(
            f"executive_summary 저장 중 DB 오류 발생 (type={clean_type}, date={clean_date}): {db_err}",
            exc_info=True,
        )
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def get_executive_summary(
    summary_type: str = "daily",
    date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, str]]:
    """
    [Phase 6-2 신규] 대시보드 최상단 탭 렌더링을 위해 `executive_summary` 테이블에서
    지정된 `summary_type`('daily' 또는 'weekly')의 요약 데이터를 조회합니다.
    - `date`가 지정된 경우 해당 날짜의 레코드를 우선 조회하며, 없으면 해당 `summary_type`의
      가장 최근(최신 `date`) 레코드를 폴백으로 반환합니다.
    - `date=None`인 경우(예: 'weekly' 탭 조회 시) 가장 최근 생성된 해당 `summary_type` 레코드를 반환합니다.

    Args:
        summary_type (str): 조회할 요약 유형 ('daily' 또는 'weekly').
        date (Optional[str]): 조회할 기준 날짜 (YYYY-MM-DD).
        db_path (Optional[Union[str, Path]]): 사용할 DB 경로.

    Returns:
        Optional[Dict[str, str]]: `{"date": "...", "summary_type": "...", "content": "...", "updated_at": "..."}` 또는 None.
    """
    clean_type = (summary_type or "daily").strip().lower()
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='executive_summary';"
        )
        if not cursor.fetchone():
            return None

        if date and str(date).strip():
            clean_date = str(date).strip()[:10]
            cursor.execute(
                """
                SELECT date, summary_type, content, updated_at
                FROM executive_summary
                WHERE summary_type = ? AND date = ?
                LIMIT 1;
                """,
                (clean_type, clean_date),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

        # 지정 날짜에 없거나 date=None인 경우 가장 최근 생성된 해당 타입의 요약을 반환
        cursor.execute(
            """
            SELECT date, summary_type, content, updated_at
            FROM executive_summary
            WHERE summary_type = ?
            ORDER BY date DESC, id DESC
            LIMIT 1;
            """,
            (clean_type,),
        )
        latest_row = cursor.fetchone()
        return dict(latest_row) if latest_row else None

    except sqlite3.Error as db_err:
        logger.error(f"executive_summary 조회 중 DB 오류 발생 (type={clean_type}, date={date}): {db_err}", exc_info=True)
        return None
    finally:
        if conn:
            conn.close()


# 하위 호환용 래퍼 함수 유지
def upsert_daily_summary(
    date: str,
    overall_summary: str,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    return upsert_executive_summary(date=date, summary_type="daily", content=overall_summary, db_path=db_path)


def get_daily_summary(
    date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, str]]:
    res = get_executive_summary(summary_type="daily", date=date, db_path=db_path)
    if res:
        res["overall_summary"] = res["content"]
    return res


def get_dashboard_data(
    date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """
    Streamlit 대시보드 시각화 및 엑셀 리포트 추출을 위해 특정 날짜(또는 전체 기간)의
    `raw_data`와 `analysis_data`를 조인(INNER JOIN)한 통합 데이터를 반환합니다.
    [Phase 6 업데이트] `r.source_url` 컬럼을 함께 조회하여 반환합니다.
    """
    conn: Optional[sqlite3.Connection] = None
    results: List[Dict[str, Any]] = []

    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()

        base_query = """
        SELECT
            r.id AS raw_id,
            r.source_type,
            r.company,
            r.doc_id,
            r.title,
            r.abstract,
            r.content,
            r.source_url,
            r.published_date,
            r.collected_at,
            a.id AS analysis_id,
            a.tech_keywords,
            a.delta_summary,
            a.anomaly_flag,
            a.anomaly_description,
            a.korean_translation,
            a.analyzed_at
        FROM raw_data r
        INNER JOIN analysis_data a ON r.id = a.raw_id
        """

        if date and str(date).strip():
            target_date_prefix = f"{str(date).strip()[:10]}%"
            where_clause = """
            WHERE r.published_date LIKE ?
               OR r.collected_at LIKE ?
               OR a.analyzed_at LIKE ?
            ORDER BY a.anomaly_flag DESC, r.published_date DESC, r.id DESC;
            """
            cursor.execute(
                base_query + where_clause,
                (target_date_prefix, target_date_prefix, target_date_prefix),
            )
        else:
            order_clause = " ORDER BY a.anomaly_flag DESC, r.published_date DESC, r.id DESC;"
            cursor.execute(base_query + order_clause)

        rows = cursor.fetchall()
        for row in rows:
            item = dict(row)
            item["tech_keywords_list"] = parse_and_normalize_keywords(item.get("tech_keywords", "[]"))
            item["anomaly_flag"] = bool(item.get("anomaly_flag", 0))
            item["source_url"] = (item.get("source_url") or "").strip()
            item["korean_translation"] = (item.get("korean_translation") or "").strip()
            results.append(item)

        logger.info(f"대시보드 데이터 조회 완료 (기준일={date or 'ALL'}): 총 {len(results)}건")
        return results

    except sqlite3.Error as db_err:
        logger.error(f"대시보드 데이터 조회 중 DB 오류 발생 (date={date}): {db_err}", exc_info=True)
        return []
    except Exception as exc:
        logger.error(f"대시보드 데이터 조회 중 예기치 못한 오류 발생 (date={date}): {exc}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()


def has_newer_analysis_since(
    since_timestamp: str,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    """
    `executive_summary`의 `updated_at` 시각 이후에 새롭게 갱신된 `analysis_data` 레코드가 있는지
    확인하여, 변경 사항이 없을 때 불필요한 `gpt-4o` 요약 재호출(토큰 소모)을 원천 차단합니다.
    """
    if not since_timestamp or not str(since_timestamp).strip():
        return True

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT MAX(analyzed_at)
            FROM analysis_data
            WHERE delta_summary IS NOT NULL AND TRIM(delta_summary) != '';
            """
        )
        row = cursor.fetchone()
        latest_analyzed = str(row[0] or "").strip() if row else ""
        if not latest_analyzed:
            return False
        return latest_analyzed > str(since_timestamp).strip()
    except sqlite3.Error:
        return True
    finally:
        if conn:
            conn.close()


def get_keyword_trends(
    limit: int = 20,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """
    대시보드 트렌드 차트용으로 `keyword_trend` 테이블에서 출현 빈도 상위 키워드 목록을 조회합니다.
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, keyword, frequency, last_seen_date
            FROM keyword_trend
            ORDER BY frequency DESC, last_seen_date DESC
            LIMIT ?;
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as db_err:
        logger.error(f"키워드 트렌드 조회 중 DB 오류 발생: {db_err}", exc_info=True)
        return []
    finally:
        if conn:
            conn.close()
