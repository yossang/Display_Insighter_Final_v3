"""
Display Foresight - Google News RSS & Clean Article Collector (collectors/fetch_news.py)

본 모듈은 Google News RSS를 활용하여 주요 디스플레이 패널사(SDC, BOE, CSOT) 및
글로벌 세트사(Apple, Samsung)의 최신 디스플레이 기술/산업 뉴스를 수집합니다.

핵심 설계 원칙:
1. Zero-Token 방어 (검색 쿼리 최적화 & 선제적 중복 제거):
   - 기업명과 디스플레이 핵심 기술 키워드('OLED', 'Display', 'Tandem', 'Micro LED', 'QD-OLED', 'Foldable' 등)를
     교집합(AND/OR)으로 조합한 Google News RSS 쿼리를 구성합니다.
   - RSS 피드에서 추출한 기사 고유 URL을 SHA-256 해시(`NEWS-<16자 해시>`)로 변환하여
     `utils.db_helper.is_duplicate_doc`으로 DB 존재 여부를 **본문 크롤링 전에 먼저 체크**합니다.
     이미 수집된 기사라면 네트워크 접속 및 본문 파싱을 즉시 패스(Pass)하여 리소스와 토큰을 절약합니다.
2. 외과 수술식 순수 본문 발췌 (Surgical Clean-Text Parsing):
   - DB에 없는 신규 기사만 접속하여 `newspaper3k` -> `readability-lxml` + `BeautifulSoup` 다중 파이프라인을 통해
     사이드바, 광고, 헤더/푸터, 네비게이션 메뉴, 스크립트, 관련기사 링크 등을 완벽히 제거한
     '순수 기사 본문(Clean Text)'만 정밀 추출합니다.
3. DB 적재 연동:
   - 정제된 기사는 `utils.db_helper.insert_raw_data(source_type='news', ...)`를 통해 안전하게 적재합니다.
"""

import email.utils
import hashlib
import json
import logging
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# 프로젝트 루트 경로를 sys.path에 추가하여 상위 패키지(utils) 임포트 보장
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.db_helper import get_existing_doc_ids, insert_raw_data, is_duplicate_doc

# 선택적 외부 라이브러리 임포트 (설치 여부에 따라 다중 폴백 체인 구성)
try:
    import feedparser  # type: ignore

    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

try:
    from newspaper import Article, Config as NewspaperConfig  # type: ignore

    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False

try:
    from readability import Document as ReadabilityDocument  # type: ignore

    HAS_READABILITY = True
except ImportError:
    HAS_READABILITY = False

# 로깅 설정
logger = logging.getLogger("DisplayForesight.NewsCollector")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# =====================================================================
# 1. 수집 대상 10대 기업 및 LGD 맞춤형 차세대 기술 키워드 OR 묶음 설정
# =====================================================================

GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"

# [Phase 2 & 3 통합 업데이트] 차세대 기술 키워드 OR 검색 묶음
NEXT_GEN_TECH_OR_CLAUSE: str = (
    '("Tandem" OR "Micro OLED" OR "OLEDoS" OR "Foldable" OR "Rollable" '
    'OR "4-Side Bending" OR "Micro Lens Array" OR "Hybrid Bonding" '
    'OR "Under Panel Sensor" OR "UPS" OR "Cathode Patterning Material" '
    'OR "CPM" OR "Privacy Mode" OR "PDL" OR "OLED" OR "Display" OR "디스플레이" OR "패널")'
)

# 10대 타겟 기업별 맞춤형 Google News RSS 검색 쿼리
# 기존 5개사(SDC, BOE, CSOT, Apple, Samsung) + 신규 5개사(LG Display, Visionox, Huawei, AUO, Innolux)
TARGET_NEWS_QUERIES: Dict[str, Dict[str, str]] = {
    "LG Display": {
        "query": f'("LG디스플레이" OR "LG Display" OR "LGD") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "SDC": {
        "query": f'("삼성디스플레이" OR "Samsung Display" OR "SDC") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "BOE": {
        "query": f'("BOE" OR "비오이") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "CSOT": {
        "query": f'("CSOT" OR "차이나스타" OR "China Star") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "Visionox": {
        "query": f'("Visionox" OR "비전옥스") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "AUO": {
        "query": f'("AUO" OR "AU Optronics" OR "에이유오") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "Innolux": {
        "query": f'("Innolux" OR "이노룩스") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "Apple": {
        "query": f'("Apple" OR "애플") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "Samsung": {
        "query": f'("삼성전자" OR "Samsung Electronics") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
    "Huawei": {
        "query": f'("Huawei" OR "화웨이") AND {NEXT_GEN_TECH_OR_CLAUSE}',
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    },
}

# 본문 크롤링 시 제거할 노이즈 HTML 태그 및 클래스/아이디 패턴
NOISE_TAGS: Tuple[str, ...] = (
    "script",
    "style",
    "noscript",
    "iframe",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "button",
    "svg",
    "canvas",
    "figure",
    "figcaption",
)

NOISE_ATTR_REGEX = re.compile(
    r"(?:sidebar|menu|nav|footer|header|comment|ad-|ads|advert|promo|banner|related|recommend|share|social|copyright|breadcrumb|pagination|widget|popup|modal|subscribe|newsletter)",
    re.IGNORECASE,
)

# 기사 본문 컨테이너 우선 탐색 셀렉터
ARTICLE_BODY_SELECTORS: Tuple[str, ...] = (
    "article",
    "[itemprop='articleBody']",
    "#dic_area",  # 네이버 뉴스 본문
    "#articleBodyContents",
    "#article-view-content-div",  # 국내 주요 IT/산업 전문지(전자신문, 디일렉 등) 흔한 본문 영역
    ".article_view",
    ".article-body",
    ".article_body",
    ".news_body",
    ".post-content",
    ".entry-content",
    "main",
)

DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =====================================================================
# 2. 유틸리티 함수 (URL 해시, 날짜 정규화, RSS 요약 정제)
# =====================================================================


def generate_news_doc_id(url: str) -> str:
    """
    기사 URL로부터 중복 방지용 고유 해시 ID(`doc_id`)를 생성합니다.
    트래킹 파라미터(utm_* 등)로 인한 중복 오탐을 줄이기 위해 정규화 후 SHA-256 해시를 생성합니다.

    Args:
        url (str): 기사 원문 또는 RSS 링크 URL.

    Returns:
        str: `NEWS-<24자리 SHA256 해시>` 형태의 고유 식별자.
    """
    clean_url = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(clean_url)
        # utm_ 등 광고/트래킹 쿼리 파라미터 제거
        query_pairs = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref"))
        ]
        normalized_url = urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(query_pairs), "")
        )
    except Exception:
        normalized_url = clean_url

    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:24]
    return f"NEWS-{digest}"


def parse_rss_date(raw_date: Optional[str], parsed_struct: Any = None) -> str:
    """
    RSS 피드의 발행일(`pubDate`, RFC 2822 형식 등)을 표준 `YYYY-MM-DD` 문자열로 변환합니다.
    """
    if parsed_struct and hasattr(parsed_struct, "tm_year"):
        try:
            return f"{parsed_struct.tm_year:04d}-{parsed_struct.tm_mon:02d}-{parsed_struct.tm_mday:02d}"
        except Exception:
            pass

    if raw_date:
        try:
            dt = email.utils.parsedate_to_datetime(raw_date.strip())
            return dt.strftime("%Y-%m-%d")
        except Exception:
            # YYYY-MM-DD 패턴 직접 탐색
            m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", str(raw_date))
            if m:
                return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return datetime.now().strftime("%Y-%m-%d")


def clean_rss_summary(raw_summary: Optional[str], title: str = "") -> str:
    """
    Google News RSS의 `<description>` 필드에 포함된 HTML 태그 및 제목 중복 텍스트를 제거하여
    깔끔한 요약문(Abstract)을 반환합니다. 요약이 없거나 제목과 동일하면 빈 문자열(`""`)을 반환합니다.
    """
    if not raw_summary:
        return ""
    soup = BeautifulSoup(raw_summary, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()

    # Google News RSS는 종종 description에 기사 제목과 언론사명만 반복하므로, 제목과 거의 같으면 빈 문자열 처리
    if not text or (title and text.startswith(title[:20]) and len(text) <= len(title) + 25):
        return ""
    return text


# =====================================================================
# 3. 외과 수술식 순수 본문 파서 (Surgical Clean-Text Extractor)
# =====================================================================


def clean_extracted_text(raw_text: str) -> str:
    """
    추출된 본문에서 기자 이메일, 저작권 문구, 무단 전재 배포 금지 등 불필요한 보일러플레이트 문장을
    제거하고 문단 간격을 정규화합니다.
    """
    if not raw_text:
        return ""

    lines: List[str] = []
    boilerplate_patterns = re.compile(
        r"(?:무단\s*전재\s*및\s*재배포\s*금지|저작권자\s*©|Copyrights?\s*©|"
        r"기자의\s*다른\s*기사\s*보기|구독하기|좋아요\s*눌러주세요|"
        r"All\s+rights\s+reserved|기사제보\s*및\s*보도자료)",
        re.IGNORECASE,
    )

    for raw_line in raw_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        # 지나치게 짧은 메뉴성 단어나 저작권/제보 문구 라인 제외
        if len(line) < 4:
            continue
        if boilerplate_patterns.search(line) and len(line) < 120:
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    # 과도한 LLM 토큰 소모 방지를 위해 핵심 본문 상단 2,500자 이내로 최적 절삭
    return cleaned[:2500].strip()


def extract_clean_article_text(url: str, html_content: Optional[str] = None, timeout: float = 12.0) -> str:
    """
    신규 기사 URL(또는 이미 가져온 HTML)로부터 사이드바, 광고, 네비게이션 메뉴를 완벽히 제거한
    '순수 기사 본문(Clean Text)'만 외과 수술식으로 추출합니다.

    추출 파이프라인 우선순위:
    1. `newspaper3k` (Article.download & parse)
    2. `readability-lxml` (Document.summary) + `BeautifulSoup` 정제
    3. `BeautifulSoup` 기반 DOM 트리 노이즈 태그/속성 외과적 제거 및 본문 셀렉터 추출

    Args:
        url (str): 기사 URL.
        html_content (Optional[str]): 사전 다운로드된 HTML 문자열 (없으면 내부에서 요청).
        timeout (float): HTTP 요청 타임아웃(초).

    Returns:
        str: 정제된 순수 기사 본문 텍스트. 추출 실패 시 빈 문자열(`""`) 반환.
    """
    if not url or not url.strip():
        return ""

    # 1차 시도: newspaper3k 라이브러리 활용
    if HAS_NEWSPAPER:
        try:
            np_config = NewspaperConfig()
            np_config.browser_user_agent = DEFAULT_HEADERS["User-Agent"]
            np_config.request_timeout = int(timeout)
            np_config.fetch_images = False
            np_config.memoize_articles = False

            article = Article(url, config=np_config)
            if html_content:
                article.download(input_html=html_content)
            else:
                article.download()
            article.parse()

            if article.text and len(article.text.strip()) >= 80:
                cleaned = clean_extracted_text(article.text)
                if len(cleaned) >= 80:
                    return cleaned
        except Exception as np_err:
            logger.debug(f"newspaper3k 본문 추출 폴백 전환 (url={url}): {np_err}")

    # HTML 다운로드 (아직 없는 경우)
    raw_html = html_content
    if not raw_html:
        try:
            with requests.Session() as session:
                resp = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
                resp.raise_for_status()
                # 인코딩 자동 보정
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                raw_html = resp.text
        except Exception as req_err:
            logger.warning(f"기사 원문 페이지 접속 실패 (url={url}): {req_err}")
            return ""

    if not raw_html or not raw_html.strip():
        return ""

    # 2차 시도: readability-lxml + BeautifulSoup 조합
    if HAS_READABILITY:
        try:
            doc = ReadabilityDocument(raw_html)
            summary_html = doc.summary(html_partial=True)
            soup_read = BeautifulSoup(summary_html, "lxml")
            for tag in soup_read(list(NOISE_TAGS)):
                tag.decompose()
            read_text = soup_read.get_text(separator="\n", strip=True)
            cleaned_read = clean_extracted_text(read_text)
            if len(cleaned_read) >= 80:
                return cleaned_read
        except Exception as read_err:
            logger.debug(f"readability-lxml 본문 추출 폴백 전환 (url={url}): {read_err}")

    # 3차 시도: BeautifulSoup 외과적 DOM 정제 (사이드바, 광고, 네비게이션 완벽 제거)
    try:
        try:
            soup = BeautifulSoup(raw_html, "lxml")
        except Exception:
            soup = BeautifulSoup(raw_html, "html.parser")

        # 3-1. 불필요한 스크립트/네비게이션/푸터/사이드바 태그 제거
        for tag in soup.find_all(list(NOISE_TAGS)):
            tag.decompose()

        # 3-2. 광고/사이드바/메뉴/추천기사 관련 class 또는 id를 가진 컨테이너 제거
        for elem in list(soup.find_all(True)):
            if elem.attrs is None:
                continue
            # body, main, article 등 최상위 루트 컨테이너는 보존
            if elem.name in ("html", "body", "main", "article"):
                continue
            cls_list = elem.get("class") or []
            cls_str = " ".join(cls_list) if isinstance(cls_list, list) else str(cls_list)
            id_str = str(elem.get("id") or "")
            role_str = str(elem.get("role") or "")
            combined_attr = f"{cls_str} {id_str} {role_str}"
            if NOISE_ATTR_REGEX.search(combined_attr):
                elem.decompose()

        # 3-3. 본문 전용 컨테이너 우선 탐색
        target_container = None
        for selector in ARTICLE_BODY_SELECTORS:
            found = soup.select_one(selector)
            if found and len(found.get_text(strip=True)) >= 100:
                target_container = found
                break

        if target_container is None:
            target_container = soup.body or soup

        # 문단(<p>, <div>) 단위로 텍스트 추출
        paragraphs = [
            p.get_text(separator=" ", strip=True)
            for p in target_container.find_all(["p", "div"])
            if len(p.get_text(strip=True)) >= 25 and not p.find(["p", "div"])
        ]
        if paragraphs:
            raw_body = "\n".join(paragraphs)
        else:
            raw_body = target_container.get_text(separator="\n", strip=True)

        return clean_extracted_text(raw_body)

    except Exception as bs_err:
        logger.error(f"BeautifulSoup 본문 추출 중 오류 발생 (url={url}): {bs_err}", exc_info=True)
        return ""


# =====================================================================
# 4. Google News RSS 수집기 클래스
# =====================================================================


class GoogleNewsCollector:
    """
    Google News RSS 피드를 조회하고 신규 기사의 순수 본문을 추출하는 프로덕션 컬렉터 클래스.
    """

    def __init__(self, request_timeout: float = 12.0) -> None:
        self.request_timeout: float = request_timeout

    @staticmethod
    def build_rss_url(query: str, hl: str = "ko", gl: str = "KR", ceid: str = "KR:ko") -> str:
        """
        검색 쿼리와 로케일 파라미터를 조합하여 Google News RSS 검색 URL을 생성합니다.
        """
        params = {
            "q": query,
            "hl": hl,
            "gl": gl,
            "ceid": ceid,
        }
        return f"{GOOGLE_NEWS_RSS_BASE}?{urllib.parse.urlencode(params)}"

    def fetch_rss_entries(self, rss_url: str) -> List[Dict[str, Any]]:
        """
        Google News RSS URL을 호출하여 기사 엔트리 목록(`title`, `link`, `summary`, `published_date`)을 반환합니다.
        `feedparser` 라이브러리를 우선 사용하며, 미설치 환경에서는 표준 XML 파서 폴백으로 동작합니다.
        """
        entries: List[Dict[str, Any]] = []

        try:
            resp = requests.get(rss_url, headers=DEFAULT_HEADERS, timeout=self.request_timeout)
            resp.raise_for_status()
            raw_xml_bytes = resp.content
        except Exception as exc:
            logger.warning(f"Google News RSS 호출 실패 (url={rss_url}): {exc}")
            return entries

        # 1. feedparser 라이브러리 사용 (권장 표준)
        if HAS_FEEDPARSER:
            parsed_feed = feedparser.parse(raw_xml_bytes)
            for entry in getattr(parsed_feed, "entries", []):
                title = str(entry.get("title", "")).strip()
                link = str(entry.get("link", "")).strip()
                summary = str(entry.get("summary", "") or entry.get("description", "")).strip()
                src_info = entry.get("source") or {}
                publisher_url = str(src_info.get("href", "") if isinstance(src_info, dict) else "").strip()
                publisher_name = str(src_info.get("title", "") if isinstance(src_info, dict) else "").strip()
                pub_date = parse_rss_date(
                    entry.get("published") or entry.get("updated"),
                    entry.get("published_parsed") or entry.get("updated_parsed"),
                )
                if title and link:
                    entries.append(
                        {
                            "title": title,
                            "link": link,
                            "publisher_url": publisher_url,
                            "publisher_name": publisher_name,
                            "summary": clean_rss_summary(summary, title=title),
                            "published_date": pub_date,
                        }
                    )
            return entries

        # 2. feedparser 미설치 환경을 위한 표준 ElementTree XML 파서 폴백
        try:
            root = ET.fromstring(raw_xml_bytes)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub_date = parse_rss_date(item.findtext("pubDate"))
                src_elem = item.find("source")
                publisher_url = (src_elem.get("url") if src_elem is not None else "") or ""
                publisher_name = (src_elem.text if src_elem is not None and src_elem.text else "").strip()
                if title and link:
                    entries.append(
                        {
                            "title": title,
                            "link": link,
                            "publisher_url": publisher_url.strip(),
                            "publisher_name": publisher_name,
                            "summary": clean_rss_summary(desc, title=title),
                            "published_date": pub_date,
                        }
                    )
        except ET.ParseError as xml_err:
            logger.error(f"RSS XML 파싱 실패: {xml_err}")

        return entries


# =====================================================================
# 5. 파이프라인 실행 및 DB 적재 메인 함수
# =====================================================================


def collect_and_store_news(
    companies: Optional[List[str]] = None,
    max_per_company: int = 10,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    주요 디스플레이 기업(SDC, BOE, CSOT, Apple, Samsung)의 최신 뉴스를 Google News RSS에서 수집하고,
    `is_duplicate_doc`으로 중복을 먼저 차단한 뒤 신규 기사만 본문을 파싱하여 `raw_data`에 적재합니다.

    Args:
        companies (Optional[List[str]]): 수집 대상 기업 리스트. 기본값은 5대 타겟 기업 전체.
        max_per_company (int): 기업당 최대 처리할 RSS 기사 건수.
        db_path (Optional[Path]): 커스텀 SQLite DB 경로.

    Returns:
        Dict[str, int]: 수집 통계 결과 딕셔너리 (`fetched`, `inserted`, `duplicates`, `filtered`, `errors`).
    """
    stats: Dict[str, int] = {
        "fetched": 0,
        "inserted": 0,
        "duplicates": 0,
        "filtered": 0,
        "errors": 0,
    }

    collector = GoogleNewsCollector()
    target_list = companies or list(TARGET_NEWS_QUERIES.keys())
    existing_doc_ids = get_existing_doc_ids(db_path=db_path)

    for company in target_list:
        q_conf = TARGET_NEWS_QUERIES.get(company)
        if not q_conf:
            logger.warning(f"지원하지 않는 기업 키입니다: {company}")
            continue

        rss_url = collector.build_rss_url(
            query=q_conf["query"],
            hl=q_conf.get("hl", "ko"),
            gl=q_conf.get("gl", "KR"),
            ceid=q_conf.get("ceid", "KR:ko"),
        )
        logger.info(f"[{company}] Google News RSS 피드 수집 시작: query={q_conf['query']}")

        try:
            entries = collector.fetch_rss_entries(rss_url)[:max_per_company]
            stats["fetched"] += len(entries)

            for entry in entries:
                try:
                    article_url = entry.get("link", "").strip()
                    title = entry.get("title", "").strip()
                    if not article_url or not title:
                        stats["filtered"] += 1
                        continue

                    # 1. URL 해시 기반 doc_id 생성 및 O(1) 메모리 Set 선제적 중복 검사 (Zero-Token 방어)
                    doc_id = generate_news_doc_id(article_url)
                    if doc_id in existing_doc_ids:
                        logger.debug(f"[{company}] 이미 수집된 기사 스킵 (본문 크롤링 생략): doc_id={doc_id}")
                        stats["duplicates"] += 1
                        continue

                    # 2. DB에 없는 신규 기사만 접속하여 순수 본문(Clean Text) 외과 수술식 발췌
                    clean_body = extract_clean_article_text(
                        article_url,
                        timeout=collector.request_timeout,
                    )

                    abstract_text = entry.get("summary", "") or ""

                    # Google News 리다이렉트 차단 등으로 본문 직접 크롤링이 불가한 경우에도
                    # 파이프라인이 중단되지 않도록 RSS 요약/제목을 폴백 본문으로 구성
                    if not clean_body:
                        clean_body = (
                            f"[원문 링크: {article_url}]\n{abstract_text}"
                            if abstract_text
                            else f"[원문 링크: {article_url}]\n{title}"
                        )

                    published_date = entry.get("published_date") or datetime.now().strftime("%Y-%m-%d")

                    # 3. DB 적재 (`source_type='news'`, `source_url=article_url` 원문 링크 포함)
                    inserted_id = insert_raw_data(
                        source_type="news",
                        company=company,
                        doc_id=doc_id,
                        title=title,
                        abstract=abstract_text,
                        content=clean_body,
                        published_date=published_date,
                        source_url=article_url,
                        db_path=db_path,
                    )

                    if inserted_id is not None:
                        existing_doc_ids.add(doc_id)
                        stats["inserted"] += 1
                    else:
                        stats["duplicates"] += 1

                    # 대상 언론사 서버 부하 방지용 짧은 예의 딜레이
                    time.sleep(0.2)

                except Exception as entry_exc:
                    logger.error(f"[{company}] 개별 뉴스 기사 처리 중 오류: {entry_exc}", exc_info=True)
                    stats["errors"] += 1

        except Exception as comp_exc:
            logger.error(f"[{company}] 뉴스 RSS 파이프라인 오류: {comp_exc}", exc_info=True)
            stats["errors"] += 1

    logger.info(
        f"Google News 수집 완료 | 피드 수신: {stats['fetched']}건, "
        f"신규 적재: {stats['inserted']}건, 중복 스킵: {stats['duplicates']}건, "
        f"필터 제외: {stats['filtered']}건, 오류: {stats['errors']}건"
    )
    return stats


if __name__ == "__main__":
    result_stats = collect_and_store_news()
    print(json.dumps(result_stats, indent=2, ensure_ascii=False))
