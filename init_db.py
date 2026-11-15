"""
Display Foresight - Database Initialization Module (init_db.py)

본 스크립트는 프로젝트 루트 경로에 SQLite3 기반의 `display_foresight.db` 데이터베이스 파일을 생성하고,
특허/뉴스 원문 수집(`raw_data` - `source_url` 포함), AI 심층 분석 결과(`analysis_data`),
동적 키워드 트렌드(`keyword_trend`), 그리고 일간/주간 AI 종합 브리핑(`executive_summary`)
총 4개 핵심 테이블 및 스키마 마이그레이션, 성능 최적화용 인덱스를 초기화합니다.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

# 로깅 설정 (B2B 프로덕션 레벨 표준 포맷)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DisplayForesight.InitDB")

# 프로젝트 루트 경로 및 DB 파일 경로 설정
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DEFAULT_DB_PATH: Path = PROJECT_ROOT / "display_foresight.db"


def _ensure_column_exists(
    cursor: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    column_def: str,
) -> None:
    """
    기존 테이블에 특정 컬럼이 없을 경우 데이터 손실 없이 `ALTER TABLE ... ADD COLUMN`으로
    스키마를 자동 마이그레이션합니다.
    """
    cursor.execute(f"PRAGMA table_info({table_name});")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if column_name not in existing_cols:
        alter_sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def};"
        cursor.execute(alter_sql)
        logger.info(f"스키마 마이그레이션 완료: {table_name}.{column_name} 컬럼 추가됨")


def init_database(db_path: Optional[Path] = None) -> bool:
    """
    SQLite3 데이터베이스 및 4개 핵심 테이블(`raw_data`, `analysis_data`, `keyword_trend`, `executive_summary`)을
    생성하고 기존 테이블(`daily_summary` -> `executive_summary` 등)의 스키마 변경 사항을 안전하게 마이그레이션합니다.

    Args:
        db_path (Optional[Path]): 생성할 DB 파일 경로. 기본값은 프로젝트 루트의 `display_foresight.db`.

    Returns:
        bool: 데이터베이스 초기화 성공 여부 (True/False)
    """
    target_path = db_path or DEFAULT_DB_PATH
    conn: Optional[sqlite3.Connection] = None

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"데이터베이스 초기화 및 스키마 점검을 시작합니다. 경로: {target_path}")
        conn = sqlite3.connect(str(target_path), timeout=30.0)
        cursor = conn.cursor()

        # SQLite 외래키(Foreign Key) 제약조건 활성화 및 WAL(Write-Ahead Logging) 모드 설정
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")

        # 1. raw_data 테이블 생성 (특허 및 뉴스 원문 저장 + source_url 컬럼 포함)
        create_raw_data_sql = """
        CREATE TABLE IF NOT EXISTS raw_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL CHECK(source_type IN ('patent', 'news')),
            company TEXT NOT NULL,
            doc_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            abstract TEXT,
            content TEXT,
            source_url TEXT DEFAULT '',
            published_date TEXT NOT NULL,
            collected_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
        """
        cursor.execute(create_raw_data_sql)
        _ensure_column_exists(cursor, "raw_data", "source_url", "TEXT DEFAULT ''")
        logger.info("테이블 확인/마이그레이션 완료: raw_data (source_url 포함)")

        # 2. analysis_data 테이블 생성 (LLM 분석 결과, 이상 기류 및 영문 특허 사전 국문 번역 저장)
        create_analysis_data_sql = """
        CREATE TABLE IF NOT EXISTS analysis_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id INTEGER NOT NULL UNIQUE,
            tech_keywords TEXT NOT NULL DEFAULT '[]',
            delta_summary TEXT NOT NULL,
            anomaly_flag INTEGER NOT NULL DEFAULT 0 CHECK(anomaly_flag IN (0, 1)),
            anomaly_description TEXT,
            korean_translation TEXT DEFAULT '',
            analyzed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (raw_id) REFERENCES raw_data (id) ON DELETE CASCADE
        );
        """
        cursor.execute(create_analysis_data_sql)
        _ensure_column_exists(cursor, "analysis_data", "korean_translation", "TEXT DEFAULT ''")
        logger.info("테이블 확인/마이그레이션 완료: analysis_data (korean_translation 포함)")

        # 3. keyword_trend 테이블 생성 (동적 추출 단일 명사 빈도 통계용)
        create_keyword_trend_sql = """
        CREATE TABLE IF NOT EXISTS keyword_trend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            frequency INTEGER NOT NULL DEFAULT 1 CHECK(frequency >= 0),
            last_seen_date TEXT NOT NULL
        );
        """
        cursor.execute(create_keyword_trend_sql)
        logger.info("테이블 확인/생성 완료: keyword_trend")

        # 4. [Phase 6-2 업데이트] executive_summary 테이블 생성 (일간 'daily' 및 주간 'weekly' 종합 요약 저장)
        # 컬럼: date (날짜), summary_type ('daily' 또는 'weekly'), content (마크다운 요약 본문)
        create_executive_summary_sql = """
        CREATE TABLE IF NOT EXISTS executive_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            summary_type TEXT NOT NULL CHECK(summary_type IN ('daily', 'weekly')),
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(date, summary_type)
        );
        """
        cursor.execute(create_executive_summary_sql)

        # 만약 이전 버전의 `daily_summary` 테이블이 존재한다면 데이터를 `executive_summary`로 자동 이관 후 정리
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_summary';"
        )
        if cursor.fetchone():
            cursor.execute(
                """
                INSERT OR IGNORE INTO executive_summary (date, summary_type, content, updated_at)
                SELECT date, 'daily', overall_summary, updated_at FROM daily_summary;
                """
            )
            cursor.execute("DROP TABLE daily_summary;")
            logger.info("레거시 daily_summary 테이블 데이터를 executive_summary(summary_type='daily')로 이관 및 정리 완료")

        logger.info("테이블 확인/생성 완료: executive_summary (daily/weekly 지원)")

        # 5. 조회 및 JOIN 성능 최적화를 위한 인덱스 생성
        index_sql_list = [
            "CREATE INDEX IF NOT EXISTS idx_raw_data_doc_id ON raw_data(doc_id);",
            "CREATE INDEX IF NOT EXISTS idx_raw_data_published_date ON raw_data(published_date);",
            "CREATE INDEX IF NOT EXISTS idx_raw_data_collected_at ON raw_data(collected_at);",
            "CREATE INDEX IF NOT EXISTS idx_raw_data_company ON raw_data(company);",
            "CREATE INDEX IF NOT EXISTS idx_analysis_data_raw_id ON analysis_data(raw_id);",
            "CREATE INDEX IF NOT EXISTS idx_analysis_data_anomaly ON analysis_data(anomaly_flag);",
            "CREATE INDEX IF NOT EXISTS idx_keyword_trend_freq ON keyword_trend(frequency DESC);",
            "CREATE INDEX IF NOT EXISTS idx_exec_summary_type_date ON executive_summary(summary_type, date DESC);",
        ]
        for idx_sql in index_sql_list:
            cursor.execute(idx_sql)

        conn.commit()
        logger.info("모든 테이블(4종) 및 인덱스 구성이 성공적으로 완료되었습니다.")
        return True

    except sqlite3.Error as db_err:
        logger.error(f"SQLite 데이터베이스 초기화 중 오류가 발생했습니다: {db_err}", exc_info=True)
        if conn:
            conn.rollback()
        return False
    except Exception as exc:
        logger.error(f"예기치 못한 시스템 오류가 발생했습니다: {exc}", exc_info=True)
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()
            logger.debug("데이터베이스 연결이 안전하게 종료되었습니다.")


if __name__ == "__main__":
    success = init_database()
    if not success:
        raise SystemExit(1)
