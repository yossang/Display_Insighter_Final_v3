"""
Display Foresight - KIPRIS + USPTO Dual Patent Collector Module (collectors/fetch_patents.py)

본 모듈은 한국 KIPRIS Plus(특허정보넷) OpenAPI와 미국 USPTO PatentsView API
(`https://api.patentsview.org/patents/query`)를 모두 호출하여 주요 디스플레이 패널사(SDC, BOE, CSOT) 및
글로벌 세트사(Apple, Samsung)의 최근 공개/등록 특허를 듀얼 파이프라인으로 수집합니다.

핵심 설계 원칙:
1. Zero-Token 방어 (IPC/CPC 교집합 필터링):
   - KIPRIS 및 USPTO 검색 쿼리 구성 시 Apple, Samsung 등 세트업체의 비(非)디스플레이 특허
     (통신 프로토콜, 배터리 화학 등)가 섞이지 않도록 디스플레이/광학/폼팩터 핵심 IPC/CPC 분류코드
     (H10K, G09G, G09F, G02F, G02B, H01L, G06F)를 `_and` 교집합 조건으로 강제합니다.
2. 외과 수술식 파싱 (Surgical Parsing for Token Optimization):
   - 명세서 전문을 배제하고 오직 '발명의 명칭(Title / patent_title)', '요약서(Abstract / patent_abstract)',
     그리고 '독립 청구항(Independent Claims)'만 정밀 추출합니다.
3. 특허청별 정확한 원문 열람 링크(`source_url`) 조합 및 DB 적재:
   - 한국 특허(`KR-...`): KIPRIS 특허 상세 조회 URL 조합
   - 미국 특허(`US-...`): Google Patents / USPTO 원문 열람 URL(`https://patents.google.com/patent/US{patent_number}/en`) 조합
   - `utils.db_helper.is_duplicate_doc`으로 중복을 선제 차단하고 신규 특허만 `insert_raw_data`로 적재합니다.
"""

from datetime import datetime, timedelta
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# 프로젝트 루트 경로를 sys.path에 추가하여 상위 패키지(utils) 임포트 보장
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.db_helper import get_existing_doc_ids, insert_raw_data, is_duplicate_doc, load_project_env

load_project_env()

# 로깅 설정
logger = logging.getLogger("DisplayForesight.PatentCollector")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# =====================================================================
# 1. 수집 대상 기업 및 Zero-Token 방어용 IPC/CPC 필터 상수 정의
# =====================================================================

DISPLAY_IPC_PREFIXES: Tuple[str, ...] = (
    "H10K",
    "G09G",
    "G09F",
    "G02F",
    "G02B",
    "H01L",
    "G06F",
)

# USPTO PatentsView CPC 서브클래스 필터 코드
USPTO_CPC_SUBCLASSES: Tuple[str, ...] = (
    "H10K",
    "G09G",
    "G09F",
    "G02F",
    "G02B",
    "G06F",
)

KIPRIS_IPC_QUERY_EXPR: str = "H10K+G09G+G09F+G02F+G02B+H01L+G06F"

# [Phase 2 & 3 통합 업데이트] LGD 맞춤형 차세대 기술 탐지망 OR 검색 키워드 (국문/영문)
# 특허의 발명의 명칭(Title), 요약서(Abstract), 청구범위(Claims) 검색 파라미터에 결합
NEXT_GEN_TECH_KEYWORDS_EN: List[str] = [
    "Tandem",
    "Micro OLED",
    "OLEDoS",
    "Foldable",
    "Rollable",
    "4-Side Bending",
    "Micro Lens Array",
    "Hybrid Bonding",
    "Under Panel Sensor",
    "UPS",
    "Cathode Patterning Material",
    "CPM",
    "Privacy Mode",
    "PDL",
    "Pixel Defining Layer",
    "Under Display Camera",
    "OLED",
]

NEXT_GEN_TECH_KEYWORDS_KO: List[str] = [
    "탠덤",
    "Tandem",
    "마이크로 OLED",
    "OLEDoS",
    "폴더블",
    "롤러블",
    "벤딩",
    "마이크로 렌즈",
    "MLA",
    "하이브리드 본딩",
    "언더 패널",
    "UPS",
    "음극 패터닝",
    "CPM",
    "프라이버시",
    "시야각",
    "화소 정의막",
    "픽셀 정의막",
    "PDL",
    "유기발광",
    "디스플레이",
]

# [Phase 2 & 3 통합 업데이트] 10대 타겟 기업 (패널 7개사 + 글로벌 세트 3개사)
# 기존 5개사(SDC, BOE, CSOT, Apple, Samsung) + 신규 5개사(LG Display, Visionox, Huawei, AUO, Innolux)
TARGET_COMPANIES: Dict[str, Dict[str, Any]] = {
    "LG Display": {
        "company_type": "self_panel",
        "applicants": ["엘지디스플레이", "LG디스플레이", "LG Display"],
        "uspto_assignees": ["LG Display Co., Ltd.", "LG Display"],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "SDC": {
        "company_type": "panel",
        "applicants": ["삼성디스플레이"],
        "uspto_assignees": ["Samsung Display Co., Ltd.", "Samsung Display"],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "BOE": {
        "company_type": "panel",
        "applicants": ["비오이", "BOE", "북경비오이"],
        "uspto_assignees": [
            "BOE Technology Group Co., Ltd.",
            "BOE Technology Group",
            "Beijing BOE Display Technology",
        ],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "CSOT": {
        "company_type": "panel",
        "applicants": ["차이나스타", "CSOT", "화싱광뎬", "티씨엘차이나스타"],
        "uspto_assignees": [
            "Shenzhen China Star Optoelectronics",
            "Wuhan China Star Optoelectronics",
            "TCL China Star Optoelectronics",
        ],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "Visionox": {
        "company_type": "panel",
        "applicants": ["비전옥스", "Visionox", "쿤산구오신", "운곡"],
        "uspto_assignees": [
            "Visionox Technology Inc.",
            "Kunshan Go-Visionox Opto-Electronics",
            "Hefei Visionox Technology",
        ],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "AUO": {
        "company_type": "panel",
        "applicants": ["에이유옵트로닉스", "AU Optronics", "AUO", "에이유오"],
        "uspto_assignees": ["AU Optronics Corporation", "AU Optronics"],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "Innolux": {
        "company_type": "panel",
        "applicants": ["이노룩스", "Innolux", "치메이이노룩스"],
        "uspto_assignees": ["InnoLux Corporation", "Innolux"],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "Apple": {
        "company_type": "set",
        "applicants": ["애플"],
        "uspto_assignees": ["Apple Inc."],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "Samsung": {
        "company_type": "set",
        "applicants": ["삼성전자"],
        "uspto_assignees": ["Samsung Electronics Co., Ltd."],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
    "Huawei": {
        "company_type": "set",
        "applicants": ["화웨이", "Huawei"],
        "uspto_assignees": [
            "Huawei Technologies Co., Ltd.",
            "Huawei Device Co., Ltd.",
        ],
        "domain_keywords": NEXT_GEN_TECH_KEYWORDS_KO,
        "uspto_title_keywords": NEXT_GEN_TECH_KEYWORDS_EN,
    },
}

# KIPRIS Plus REST OpenAPI 엔드포인트
KIPRIS_BASE_URL = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice"
KIPRIS_ADV_SEARCH_ENDPOINT = f"{KIPRIS_BASE_URL}/getAdvancedSearch"
KIPRIS_CLAIM_INFO_ENDPOINT = f"{KIPRIS_BASE_URL}/getClaimInfo"
KIPRIS_BIBLIO_DETAIL_ENDPOINT = f"{KIPRIS_BASE_URL}/getBibliographyDetailInfoSearch"

# USPTO PatentsView API 엔드포인트 (마스터 컨텍스트 지정 URL)
USPTO_PATENTSVIEW_URL = "https://api.patentsview.org/patents/query"


# =====================================================================
# 2. 외과 수술식 파서 (한국어 및 영문 독립 청구항 정밀 추출)
# =====================================================================

# 종속 청구항(Dependent Claim) 판별용 정규표현식 (국문 + 영문 특허 청구항 완벽 대응)
# - 국문: "제 1 항에 있어서", "제1항 내지 제3항 중 어느 한 항에 있어서", "청구항 1에 있어서" 등
# - 영문: "of claim 1", "according to claim 1", "as recited in claim 2", "The method of any of claims 1-5" 등
DEPENDENT_CLAIM_REGEX = re.compile(
    r"(?:"
    r"제\s*\d+\s*항(?:\s*(?:내지|또는|및|,|~|-)\s*제?\s*\d+\s*항)*\s*(?:중\s*어느\s*한\s*항\s*)?에\s*있어서"
    r"|청구항\s*\d+(?:\s*(?:내지|또는|및|,|~|-)\s*\d+)*\s*(?:중\s*어느\s*한\s*항\s*)?에\s*있어서"
    r"|제\s*\d+\s*항\s*또는\s*제\s*\d+\s*항"
    r"|제\s*\d+\s*항에\s*따른"
    r"|\b(?:according\s+to|of|as\s+recited\s+in|as\s+claimed\s+in|as\s+defined\s+in|in\s+accordance\s+with)\s+(?:any\s+(?:one\s+)?of\s+)?claims?\s+\d+"
    r"|\bclaim\s+\d+\s*,\s*wherein\b"
    r")",
    re.IGNORECASE,
)

# 청구항 번호 분리용 정규표현식 (국문 `[청구항 1]` 및 영문 `1. A display...`, `Claim 1.` 등 대응)
CLAIM_SPLIT_REGEX = re.compile(
    r"(?:^|\n)\s*(?:\[\s*청구항\s*(\d+)\s*\]|【\s*청구항\s*(\d+)\s*】|청구항\s*(\d+)\s*[\.:\)]?|claim\s+(\d+)\s*[\.:\)]?|(?<!\d)(\d+)\s*\.\s+(?=[가-힣A-Za-z]))",
    re.IGNORECASE | re.MULTILINE,
)


def is_independent_claim(claim_text: str) -> bool:
    """
    단일 청구항 텍스트(국문 또는 영문)가 '독립 청구항(Independent Claim)'인지 판별합니다.
    """
    cleaned = claim_text.strip()
    if not cleaned or len(cleaned) < 15:
        return False

    if re.match(r"^[\(\[\s]*(?:삭제|canceled|cancelled)[\)\]\s\.]*$", cleaned, flags=re.IGNORECASE):
        return False

    if DEPENDENT_CLAIM_REGEX.search(cleaned):
        return False

    return True


def extract_independent_claims(raw_claims: Any, is_english: bool = False) -> str:
    """
    전체 청구범위(문자열, 문자열 리스트, 또는 USPTO claim 딕셔너리 리스트)에서
    종속항을 제거하고 오직 '독립 청구항(Independent Claims)'만 외과 수술식으로 추출합니다.

    Args:
        raw_claims (Any): KIPRIS 또는 USPTO에서 수신한 청구범위 데이터.
        is_english (bool): 영문 특허 여부 (True일 경우 `[Independent Claim N]` 헤더 사용 가능).

    Returns:
        str: 정제된 독립 청구항 결합 문자열.
    """
    if not raw_claims:
        return ""

    candidate_claims: List[Tuple[int, str]] = []

    # 1. 리스트 형태로 전달된 경우 (USPTO JSON `claims` 배열 또는 KIPRIS 리스트)
    if isinstance(raw_claims, list):
        for idx, item in enumerate(raw_claims, start=1):
            if isinstance(item, dict):
                # USPTO PatentsView claim 객체: {"claim_number": "1", "claim_text": "...", "claim_dependent": ...}
                c_text = str(item.get("claim_text") or item.get("text") or item.get("claim") or "").strip()
                c_num_raw = str(item.get("claim_number") or item.get("num") or idx).strip()
                c_num = int(re.sub(r"[^0-9]", "", c_num_raw) or idx)
                c_dep = item.get("claim_dependent")
                # USPTO 메타데이터에서 이미 종속항 여부가 명시된 경우 즉시 필터링
                if c_dep not in (None, "", "false", "False", "0", 0, False):
                    continue
                c_text = re.sub(r"<[^>]+>", " ", c_text)
                c_text = re.sub(r"\s+", " ", c_text).strip()
                if c_text:
                    candidate_claims.append((c_num, c_text))
            else:
                text = str(item).strip()
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    candidate_claims.append((idx, text))

    # 2. 단일 문자열로 전달된 경우
    elif isinstance(raw_claims, str):
        cleaned_raw = re.sub(r"<br\s*/?>", "\n", raw_claims, flags=re.IGNORECASE)
        cleaned_raw = re.sub(r"<[^>]+>", " ", cleaned_raw)

        matches = list(CLAIM_SPLIT_REGEX.finditer(cleaned_raw))
        if matches:
            for i, match in enumerate(matches):
                claim_num_str = next((g for g in match.groups() if g is not None), str(i + 1))
                claim_num = int(claim_num_str) if claim_num_str.isdigit() else (i + 1)
                start_pos = match.end()
                end_pos = matches[i + 1].start() if (i + 1) < len(matches) else len(cleaned_raw)
                claim_body = cleaned_raw[start_pos:end_pos].strip()
                claim_body = re.sub(r"\s+", " ", claim_body).strip()
                if claim_body:
                    candidate_claims.append((claim_num, claim_body))
        else:
            single_body = re.sub(r"\s+", " ", cleaned_raw).strip()
            if single_body:
                candidate_claims.append((1, single_body))

    # 3. 독립 청구항만 필터링
    independent_list: List[str] = []
    for num, body in candidate_claims:
        body_clean = re.sub(
            r"^(?:\[\s*청구항\s*\d+\s*\]|【\s*청구항\s*\d+\s*】|claim\s+\d+\s*[\.:\)]?|\d+\s*\.)\s*",
            "",
            body,
            flags=re.IGNORECASE,
        ).strip()
        if is_independent_claim(body_clean):
            header = f"[Independent Claim {num}]" if is_english else f"[독립항 - 청구항 {num}]"
            independent_list.append(f"{header} {body_clean}")

    # 폴백: 제1항 보존
    if not independent_list and candidate_claims:
        first_num, first_body = candidate_claims[0]
        first_body_clean = re.sub(
            r"^(?:\[\s*청구항\s*\d+\s*\]|【\s*청구항\s*\d+\s*】|claim\s+\d+\s*[\.:\)]?|\d+\s*\.)\s*",
            "",
            first_body,
            flags=re.IGNORECASE,
        ).strip()
        if first_body_clean:
            header = f"[Independent Claim {first_num}]" if is_english else f"[독립항 - 청구항 {first_num}]"
            independent_list.append(f"{header} {first_body_clean}")

    return "\n\n".join(independent_list)


def has_valid_display_ipc(ipc_string: str) -> bool:
    """
    특허의 IPC/CPC 코드 문자열에 디스플레이/폼팩터 핵심 분류(DISPLAY_IPC_PREFIXES)가 포함되어 있는지 검증합니다.
    """
    if not ipc_string:
        return False
    upper_ipc = ipc_string.upper().replace(" ", "")
    return any(prefix in upper_ipc for prefix in DISPLAY_IPC_PREFIXES)


def normalize_date_str(raw_date: Optional[str]) -> str:
    """
    날짜 형식(예: '20261115', '2026-11-15')을 표준 'YYYY-MM-DD'로 정규화합니다.
    """
    if not raw_date:
        return datetime.now().strftime("%Y-%m-%d")
    digits = re.sub(r"[^0-9]", "", str(raw_date))
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return datetime.now().strftime("%Y-%m-%d")


def build_kipris_patent_url(item: Dict[str, str], raw_no: str) -> str:
    """
    한국 KIPRIS 수집 특허의 원문 열람 URL을 생성합니다.
    (참고: KIPRIS 내부 프레임 URL `biblioa.do?method=biblioFrame`은 외부 브라우저 직접 접속 시
     세션 오류로 차단될 수 있으므로, 세션 없이 즉시 원문 명세서·도면·청구항 전문 열람이 가능한
     Google Patents KR 원문/검색 링크를 기본 제공하며, KIPRIS 직접 URL이 응답에 포함된 경우 이를 우선 사용합니다.)
    """
    for url_key in ("kiprisUrl", "detailUrl", "docUrl"):
        candidate = (item.get(url_key) or "").strip()
        if candidate.startswith(("http://", "https://")) and "biblioFrame" not in candidate:
            return candidate

    reg_no = re.sub(r"[^0-9]", "", str(item.get("registerNumber") or ""))
    if reg_no:
        # 등록번호(예: 1021836100000 -> 앞 9자리 102183610) 기반 Google Patents KR 직행 URL
        short_reg = reg_no[:9] if len(reg_no) >= 9 and reg_no.startswith("10") else reg_no
        return f"https://patents.google.com/patent/KR{short_reg}B1/ko"

    open_no = re.sub(r"[^0-9]", "", str(item.get("openNumber") or item.get("publicationNumber") or ""))
    if open_no:
        # 공개번호(예: 1020240012345) 기반 Google Patents KR 직행 URL
        return f"https://patents.google.com/patent/KR{open_no}A/ko"

    title = (item.get("inventionTitle") or item.get("inventionTitleEng") or "").strip()
    clean_no = re.sub(r"[^0-9]", "", str(raw_no))
    if title:
        q_str = f"{title} {clean_no}".strip()
        return f"https://patents.google.com/?q={urllib.parse.quote(q_str)}"
    if clean_no:
        return f"https://patents.google.com/?q=KR{clean_no}"
    return "https://patents.google.com/"


def build_uspto_patent_url(patent_number: str) -> str:
    """
    미국 USPTO 특허번호(`patent_number`)를 기반으로 브라우저 새 창에서 즉시 열람 가능한
    공식 원문 링크(`https://patents.google.com/patent/US{patent_number}/en`)를 생성합니다.
    """
    clean_no = re.sub(r"[^0-9A-Za-z]", "", str(patent_number)).upper()
    if clean_no.startswith("US"):
        clean_no = clean_no[2:]
    if clean_no:
        return f"https://patents.google.com/patent/US{clean_no}/en"
    return "https://ppubs.uspto.gov/pubwebapp/"


# =====================================================================
# 3. 한국 KIPRIS OpenAPI 클라이언트 클래스
# =====================================================================


class KiprisPatentCollector:
    """
    KIPRIS Plus OpenAPI와 통신하여 한국 특허 검색 및 독립 청구항 추출을 수행하는 클래스.
    """

    def __init__(self, api_key: Optional[str] = None, request_timeout: float = 20.0) -> None:
        raw_key = api_key or os.getenv("KIPRIS_API_KEY", "").strip()
        self.api_key: str = urllib.parse.unquote(raw_key) if "%" in raw_key else raw_key
        self.request_timeout: float = request_timeout

    def _build_url(self, endpoint: str, params: Dict[str, Any]) -> str:
        encoded_params = urllib.parse.urlencode(params, doseq=True)
        encoded_key = urllib.parse.quote(self.api_key, safe="")
        return f"{endpoint}?{encoded_params}&ServiceKey={encoded_key}"

    def _http_get(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": "DisplayForesight-B2B-Collector/1.0",
            "Accept": "application/xml, application/json, text/xml, */*",
        }
        for attempt in range(1, 3):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    return resp.read().decode(charset, errors="replace")
            except Exception as exc:
                logger.warning(f"KIPRIS API HTTP 요청 실패 (시도 {attempt}/2): {exc}")
                if attempt < 2:
                    time.sleep(1.5)
        return None

    @staticmethod
    def parse_search_response(raw_response: str) -> List[Dict[str, str]]:
        if not raw_response or not raw_response.strip():
            return []

        stripped = raw_response.strip()
        items: List[Dict[str, str]] = []

        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(stripped)
                body = data.get("response", {}).get("body", {}) or data.get("body", {}) or data
                raw_items = (
                    body.get("items", {}).get("item", [])
                    if isinstance(body.get("items"), dict)
                    else body.get("items", [])
                )
                if isinstance(raw_items, dict):
                    raw_items = [raw_items]
                for entry in raw_items:
                    if isinstance(entry, dict):
                        items.append({k: str(v or "").strip() for k, v in entry.items()})
                return items
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON 응답 파싱 실패: {json_err}")
                return []

        try:
            root = ET.fromstring(stripped)
            result_code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
            result_msg = root.findtext(".//resultMsg") or root.findtext(".//errMsg")
            if result_code and result_code not in ("00", "000", "INFO-000"):
                logger.warning(f"KIPRIS API 응답 경고/오류: code={result_code}, msg={result_msg}")

            target_tags = {
                "applicationNumber",
                "applicationDate",
                "openNumber",
                "openDate",
                "registerNumber",
                "registerDate",
                "publicationNumber",
                "publicationDate",
                "inventionTitle",
                "inventionTitleEng",
                "astrtCont",
                "ipcNumber",
                "applicantName",
                "claim",
                "claimScope",
            }
            for item_elem in root.findall(".//item"):
                record: Dict[str, str] = {}
                for child in item_elem:
                    tag_name = child.tag.split("}")[-1]
                    if tag_name in target_tags and child.text:
                        record[tag_name] = child.text.strip()
                if record.get("applicationNumber") or record.get("inventionTitle"):
                    items.append(record)

        except ET.ParseError as xml_err:
            logger.error(f"XML 응답 파싱 오류: {xml_err}")

        return items

    @staticmethod
    def parse_claims_response(raw_response: str) -> str:
        if not raw_response or not raw_response.strip():
            return ""

        stripped = raw_response.strip()
        raw_claim_texts: List[str] = []

        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(stripped)

                def _collect_claims(node: Any) -> None:
                    if isinstance(node, dict):
                        for k, v in node.items():
                            if k.lower() in ("claim", "claimcontent", "claimtext", "claimscope") and isinstance(v, str):
                                raw_claim_texts.append(v)
                            else:
                                _collect_claims(v)
                    elif isinstance(node, list):
                        for elem in node:
                            _collect_claims(elem)

                _collect_claims(data)
            except json.JSONDecodeError:
                pass
        else:
            try:
                root = ET.fromstring(stripped)
                for elem in root.iter():
                    tag_lower = elem.tag.split("}")[-1].lower()
                    if tag_lower in ("claim", "claimcontent", "claimtext", "claimscope") and elem.text:
                        text_val = elem.text.strip()
                        if text_val:
                            raw_claim_texts.append(text_val)
            except ET.ParseError:
                fallback_matches = re.findall(
                    r"<(?:claim|claimScope|claimContent)[^>]*>(.*?)</(?:claim|claimScope|claimContent)>",
                    stripped,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                raw_claim_texts.extend(fallback_matches)

        if not raw_claim_texts:
            return ""

        if len(raw_claim_texts) == 1:
            return extract_independent_claims(raw_claim_texts[0])
        return extract_independent_claims(raw_claim_texts)

    def fetch_independent_claims(self, application_number: str) -> str:
        clean_app_no = re.sub(r"[^0-9]", "", str(application_number))
        if not clean_app_no:
            return ""

        claim_url = self._build_url(KIPRIS_CLAIM_INFO_ENDPOINT, {"applicationNumber": clean_app_no})
        raw_resp = self._http_get(claim_url)
        if raw_resp:
            indep_claims = self.parse_claims_response(raw_resp)
            if indep_claims:
                return indep_claims

        biblio_url = self._build_url(KIPRIS_BIBLIO_DETAIL_ENDPOINT, {"applicationNumber": clean_app_no})
        biblio_resp = self._http_get(biblio_url)
        if biblio_resp:
            return self.parse_claims_response(biblio_resp)

        return ""

    def search_company_patents(
        self,
        company_key: str,
        num_of_rows: int = 15,
        page_no: int = 1,
    ) -> List[Dict[str, str]]:
        config = TARGET_COMPANIES.get(company_key)
        if not config:
            return []

        applicant_query = "+".join(config["applicants"])
        domain_keywords: List[str] = config.get("domain_keywords", [])

        params: Dict[str, Any] = {
            "applicant": applicant_query,
            "ipcNumber": KIPRIS_IPC_QUERY_EXPR,
            "patent": "true",
            "utility": "true",
            "numOfRows": str(num_of_rows),
            "pageNo": str(page_no),
            "sortSpec": "PD",
            "descSort": "true",
        }
        # KIPRIS getAdvancedSearch: word 파라미터는 발명의 명칭(Title), 요약(Abstract), 청구범위(Claims)를
        # 포괄 검색하며 '+' 기호는 OR 조건으로 동작합니다.
        if domain_keywords:
            params["word"] = "+".join(domain_keywords)

        url = self._build_url(KIPRIS_ADV_SEARCH_ENDPOINT, params)
        logger.info(f"[KIPRIS | {company_key}] 특허 검색 요청 (IPC={KIPRIS_IPC_QUERY_EXPR})")

        raw_xml = self._http_get(url)
        if not raw_xml:
            return []

        parsed_items = self.parse_search_response(raw_xml)
        verified_items: List[Dict[str, str]] = []
        for item in parsed_items:
            ipc_val = item.get("ipcNumber", "")
            if ipc_val and not has_valid_display_ipc(ipc_val):
                continue
            verified_items.append(item)

        return verified_items


# =====================================================================
# 4. [Phase 6 & 7 신규] 미국 USPTO PatentsView API 클라이언트 클래스
# =====================================================================


class UsptoPatentsViewCollector:
    """
    미국 USPTO PatentsView API (`https://api.patentsview.org/patents/query`)를 호출하여
    Apple, Samsung, SDC, BOE, CSOT의 미국 특허(`patent_title`, `patent_abstract`, 독립 `claim`)를
    수집하는 클래스입니다.
    """

    def __init__(self, api_key: Optional[str] = None, request_timeout: float = 20.0) -> None:
        self.api_key: str = (api_key or os.getenv("USPTO_API_KEY", "")).strip()
        self.request_timeout: float = request_timeout

    @staticmethod
    def build_patentsview_payload(
        company_key: str,
        per_page: int = 15,
        since_days: int = 365,
    ) -> Dict[str, Any]:
        """
        USPTO PatentsView 쿼리 페이로드를 조립합니다.
        Apple, Samsung, SDC, BOE, CSOT의 출원인(`assignee_organization`) 조건과
        디스플레이/폼팩터 CPC 분류 코드(`cpc_subclass_id`: H10K, G09G, G06F 등)를
        `_and` 조건으로 엄격히 결합합니다.
        """
        config = TARGET_COMPANIES.get(company_key, {})
        assignees: List[str] = config.get("uspto_assignees", [company_key])
        title_keywords: List[str] = config.get("uspto_title_keywords", [])

        since_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")

        # 1) 출원인(Assignee) 조건 (_or)
        assignee_clauses = [{"_contains": {"assignee_organization": name}} for name in assignees]
        assignee_cond: Dict[str, Any] = (
            {"_or": assignee_clauses} if len(assignee_clauses) > 1 else assignee_clauses[0]
        )

        # 2) 디스플레이/폼팩터 CPC 서브클래스 조건 (_or)
        cpc_clauses = [{"cpc_subclass_id": code} for code in USPTO_CPC_SUBCLASSES]
        cpc_cond: Dict[str, Any] = {"_or": cpc_clauses}

        # 3) _and 교집합 결합 (Zero-Token 방어)
        and_conditions: List[Dict[str, Any]] = [
            {"_gte": {"patent_date": since_date}},
            assignee_cond,
            cpc_cond,
        ]

        # [Phase 2 & 3 통합 업데이트] 차세대 핵심 기술 키워드가 Title, Abstract, Claims 중
        # 어느 한 곳이라도 포함된 특허를 포착하도록 `_or` 조건으로 결합
        if title_keywords:
            kw_expr = " ".join(title_keywords)
            kw_clauses = [
                {"_text_any": {"patent_title": kw_expr}},
                {"_text_any": {"patent_abstract": kw_expr}},
                {"_text_any": {"claim_text": kw_expr}},
            ]
            and_conditions.append({"_or": kw_clauses})

        payload: Dict[str, Any] = {
            "q": {"_and": and_conditions},
            "f": [
                "patent_number",
                "patent_title",
                "patent_date",
                "patent_abstract",
                "assignee_organization",
                "cpc_subclass_id",
                "claim_text",
                "claim_number",
                "claim_dependent",
            ],
            "o": {"per_page": per_page, "page": 1},
            "s": [{"patent_date": "desc"}],
        }
        return payload

    @staticmethod
    def parse_patentsview_response(raw_json_str: str) -> List[Dict[str, Any]]:
        """
        USPTO PatentsView JSON 응답에서 `patent_title`, `patent_abstract`, 그리고
        독립 청구항(`independent_claims`) 및 원문 링크(`source_url`)를 추출합니다.
        """
        if not raw_json_str or not raw_json_str.strip():
            return []

        try:
            data = json.loads(raw_json_str)
        except json.JSONDecodeError as err:
            logger.error(f"USPTO PatentsView JSON 파싱 오류: {err}")
            return []

        patents_list = data.get("patents") or data.get("data") or []
        if not isinstance(patents_list, list):
            return []

        results: List[Dict[str, Any]] = []
        for pat in patents_list:
            if not isinstance(pat, dict):
                continue

            pat_no = str(pat.get("patent_number") or "").strip()
            title = str(pat.get("patent_title") or "").strip()
            abstract = str(pat.get("patent_abstract") or "").strip()
            pat_date = normalize_date_str(pat.get("patent_date"))

            if not pat_no or not title:
                continue

            # 청구항 추출: nested `claims` 배열 또는 top-level `claim_text`
            raw_claims = pat.get("claims")
            if not raw_claims and pat.get("claim_text"):
                if isinstance(pat["claim_text"], list):
                    raw_claims = pat["claim_text"]
                else:
                    raw_claims = [str(pat["claim_text"])]

            indep_claims = extract_independent_claims(raw_claims, is_english=True)
            if not indep_claims:
                indep_claims = (
                    f"[Independent Claim Fallback - Abstract] {abstract}"
                    if abstract
                    else "[Independent Claim Not Available]"
                )

            source_url = build_uspto_patent_url(pat_no)
            results.append(
                {
                    "doc_id": f"US-{pat_no}",
                    "patent_number": pat_no,
                    "title": title,
                    "abstract": abstract,
                    "content": indep_claims,
                    "published_date": pat_date,
                    "source_url": source_url,
                }
            )

        return results

    def search_company_patents(
        self,
        company_key: str,
        per_page: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        USPTO PatentsView API에 POST/GET 쿼리를 전송하여 특정 기업의 미국 디스플레이 특허를 수집합니다.
        """
        payload = self.build_patentsview_payload(company_key=company_key, per_page=per_page)
        headers: Dict[str, str] = {
            "User-Agent": "DisplayForesight-B2B-Collector/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["X-Api-Key"] = self.api_key

        logger.info(
            f"[USPTO | {company_key}] PatentsView API 검색 요청 (CPC={USPTO_CPC_SUBCLASSES})"
        )

        try:
            resp = requests.post(
                USPTO_PATENTSVIEW_URL,
                json=payload,
                headers=headers,
                timeout=self.request_timeout,
            )
            if resp.status_code == 200 and resp.text:
                return self.parse_patentsview_response(resp.text)
            logger.warning(
                f"[USPTO | {company_key}] PatentsView 응답 상태 코드={resp.status_code}: {resp.text[:200]}"
            )
        except Exception as exc:
            logger.warning(f"[USPTO | {company_key}] PatentsView API 호출 실패: {exc}")

        return []


# =====================================================================
# 5. KIPRIS + USPTO 듀얼 파이프라인 실행 및 DB 적재 메인 함수
# =====================================================================


def collect_and_store_patents(
    companies: Optional[List[str]] = None,
    max_per_company: int = 10,
    api_key: Optional[str] = None,
    include_uspto: bool = True,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """
    한국 KIPRIS와 미국 USPTO(PatentsView API) 양쪽에서 주요 기업(SDC, BOE, CSOT, Apple, Samsung)의
    최신 디스플레이 특허를 수집하여 중복 체크(`is_duplicate_doc`) 후 `raw_data`에 적재합니다.
    """
    stats: Dict[str, int] = {
        "fetched": 0,
        "inserted": 0,
        "kipris_inserted": 0,
        "uspto_inserted": 0,
        "duplicates": 0,
        "filtered": 0,
        "errors": 0,
    }

    target_list = companies or list(TARGET_COMPANIES.keys())
    existing_doc_ids = get_existing_doc_ids(db_path=db_path)

    # -----------------------------------------------------------------
    # Part A: 한국 KIPRIS 특허 수집
    # -----------------------------------------------------------------
    kipris_collector = KiprisPatentCollector(api_key=api_key)
    if kipris_collector.api_key and kipris_collector.api_key != "your_kipris_openapi_service_key_here":
        for company in target_list:
            try:
                patent_items = kipris_collector.search_company_patents(
                    company_key=company,
                    num_of_rows=max_per_company,
                )
                stats["fetched"] += len(patent_items)

                for item in patent_items:
                    try:
                        app_no = re.sub(r"[^0-9]", "", item.get("applicationNumber", ""))
                        open_no = re.sub(r"[^0-9]", "", item.get("openNumber", ""))
                        reg_no = re.sub(r"[^0-9]", "", item.get("registerNumber", ""))

                        raw_no = app_no or open_no or reg_no
                        if not raw_no:
                            stats["filtered"] += 1
                            continue

                        doc_id = f"KR-{raw_no}"
                        if doc_id in existing_doc_ids:
                            stats["duplicates"] += 1
                            continue

                        title = (
                            item.get("inventionTitle")
                            or item.get("inventionTitleEng")
                            or ""
                        ).strip()
                        abstract = (item.get("astrtCont") or "").strip()
                        if not title:
                            stats["filtered"] += 1
                            continue

                        inline_claim = item.get("claim") or item.get("claimScope") or ""
                        if inline_claim:
                            independent_claims = extract_independent_claims(inline_claim)
                        elif app_no:
                            independent_claims = kipris_collector.fetch_independent_claims(app_no)
                            time.sleep(0.2)
                        else:
                            independent_claims = ""

                        if not independent_claims:
                            independent_claims = (
                                f"[독립항 미공개/요약 대체] {abstract}"
                                if abstract
                                else "[독립항 정보 없음]"
                            )

                        raw_pub_date = (
                            item.get("openDate")
                            or item.get("publicationDate")
                            or item.get("registerDate")
                            or item.get("applicationDate")
                        )
                        published_date = normalize_date_str(raw_pub_date)
                        patent_source_url = build_kipris_patent_url(item, raw_no)

                        inserted_id = insert_raw_data(
                            source_type="patent",
                            company=company,
                            doc_id=doc_id,
                            title=title,
                            abstract=abstract,
                            content=independent_claims,
                            published_date=published_date,
                            source_url=patent_source_url,
                            db_path=db_path,
                        )
                        if inserted_id is not None:
                            existing_doc_ids.add(doc_id)
                            stats["inserted"] += 1
                            stats["kipris_inserted"] += 1
                        else:
                            stats["duplicates"] += 1
                    except Exception as item_exc:
                        logger.error(f"[KIPRIS | {company}] 개별 특허 처리 오류: {item_exc}", exc_info=True)
                        stats["errors"] += 1
            except Exception as comp_exc:
                logger.error(f"[KIPRIS | {company}] 기업 특허 파이프라인 오류: {comp_exc}", exc_info=True)
                stats["errors"] += 1
    else:
        logger.warning(
            "KIPRIS_API_KEY가 설정되지 않아 한국 KIPRIS 수집을 건너뛰고 미국 USPTO 수집으로 진행합니다."
        )

    # -----------------------------------------------------------------
    # Part B: 미국 USPTO (PatentsView API) 특허 수집
    # -----------------------------------------------------------------
    if include_uspto:
        uspto_collector = UsptoPatentsViewCollector()
        for company in target_list:
            try:
                us_items = uspto_collector.search_company_patents(
                    company_key=company,
                    per_page=max_per_company,
                )
                stats["fetched"] += len(us_items)

                for u_item in us_items:
                    try:
                        doc_id = u_item["doc_id"]
                        if doc_id in existing_doc_ids:
                            stats["duplicates"] += 1
                            continue

                        inserted_id = insert_raw_data(
                            source_type="patent",
                            company=company,
                            doc_id=doc_id,
                            title=u_item["title"],
                            abstract=u_item["abstract"],
                            content=u_item["content"],
                            published_date=u_item["published_date"],
                            source_url=u_item["source_url"],
                            db_path=db_path,
                        )
                        if inserted_id is not None:
                            existing_doc_ids.add(doc_id)
                            stats["inserted"] += 1
                            stats["uspto_inserted"] += 1
                        else:
                            stats["duplicates"] += 1
                    except Exception as us_item_exc:
                        logger.error(f"[USPTO | {company}] 개별 특허 처리 오류: {us_item_exc}", exc_info=True)
                        stats["errors"] += 1
                time.sleep(0.2)
            except Exception as us_comp_exc:
                logger.error(f"[USPTO | {company}] 기업 특허 수집 오류: {us_comp_exc}", exc_info=True)
                stats["errors"] += 1

    logger.info(
        f"듀얼 특허(KIPRIS+USPTO) 수집 완료 | 조회: {stats['fetched']}건, "
        f"총 신규 적재: {stats['inserted']}건 (KIPRIS: {stats['kipris_inserted']}건, USPTO: {stats['uspto_inserted']}건), "
        f"중복 스킵: {stats['duplicates']}건, 필터 제외: {stats['filtered']}건, 오류: {stats['errors']}건"
    )
    return stats


if __name__ == "__main__":
    result_stats = collect_and_store_patents()
    print(json.dumps(result_stats, indent=2, ensure_ascii=False))
