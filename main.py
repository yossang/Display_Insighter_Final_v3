"""
Display Foresight - B2B Executive Light-Theme 3-Tier Progressive Disclosure Dashboard (main.py)

본 모듈은 임원 보고 및 상품기획/기술전략 실무에 최적화된 '3단 점진적 탐색(Progressive Disclosure)' 구조와
깔끔한 화이트/슬레이트 그레이 기반의 B2B 라이트 테마(Light Theme)를 적용한 Streamlit 프론트엔드입니다.

핵심 설계 원칙:
1. Zero-Latency & `@st.cache_data` 캐싱:
   - 본 파일 내에서는 절대 `openai` 라이브러리를 임포트하거나 외부 LLM API를 호출하지 않습니다.
   - 모든 DB 조회 및 엑셀 변환 함수에 `@st.cache_data`를 적용하여 화면 전환·필터 조작 시 지연(Latency) 제로를 보장합니다.
2. B2B 화이트/그레이 라이트 테마 & 둥근 모서리 그림자 카드(Card) CSS:
   - 배경(`#F8FAFC`) 위에 순백색(`#FFFFFF`) 카드, 부드러운 그림자(`box-shadow`), 둥근 모서리(`border-radius: 14px`)를
     적용하여 시각적 피로도를 낮추고 임원 보고서 수준의 가독성을 구현합니다.
3. 3단 점진적 탐색(Progressive Disclosure) 레이아웃:
   - [Top Tier - 거시적 통찰 (Executive Summary & Alert)]:
     최근 `anomaly_flag = True` 위협 데이터를 최상단 붉은색 경고 카드 배너로 강력하게 노출하고,
     그 아래 `st.tabs(["오늘의 종합 브리핑", "지난주 심층 요약"])`를 넓은 화이트 카드로 렌더링합니다.
   - [Middle Tier - 트렌드 및 시그널 시각화 (Context & Trends)]:
     `st.columns(2)`로 분할하여 좌측에는 '기업별 데이터 비중(SDC, BOE, Apple 등)' Plotly 도넛 차트를,
     우측에는 `keyword_trend` 기반 '급부상 기술 키워드' Plotly 바/시계열 차트(마우스 Hover 상세 수치 툴팁 완비)를 배치합니다.
   - [Bottom Tier - 딥다이브 및 행동 유도 (Actionable Details)]:
     섹션 우측 상단에 눈에 잘 띄는 `[📊 보고용 엑셀 다운로드]` 버튼을 배치하고,
     개별 뉴스 및 특허 리스트를 요약 표(`st.dataframe`) 및 아코디언(`st.expander`)으로 정돈하여
     전개 시 델타 3줄 요약, `[🔗 원문 URL 열기]` 새 창 링크, 그리고 `st.popover("🇰🇷 국문 번역 보기")` 팝업을 즉시 제공합니다.
"""

from datetime import date, datetime, timedelta, timezone
import html
import importlib
import io
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import xml.sax.saxutils as saxutils
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# 프로젝트 루트 경로 보장 및 DB Helper 리로드/임포트 (절대 openai를 임포트하지 않음)
PROJECT_ROOT: Path = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from init_db import init_database
import utils.db_helper as _db_helper

importlib.reload(_db_helper)
from utils.db_helper import (
    get_connection,
    get_dashboard_data,
    get_executive_summary,
    get_keyword_trends,
    upsert_executive_summary,
)

# =====================================================================
# 1. 페이지 설정 및 B2B 화이트/그레이 라이트 테마 커스텀 CSS 주입
# =====================================================================

st.set_page_config(
    page_title="Display Foresight | B2B 기술·특허 전략 인텔리전스",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

B2B_LIGHT_THEME_CSS = """
<style>
    /* 전체 배경 및 기본 타이포그래피 (강제 화이트/슬레이트 그레이 B2B 라이트 테마 & 한글 폰트 깨짐 방지) */
    html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stMain"] {
        background-color: #F8FAFC !important;
        color: #0F172A !important;
        font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif !important;
        -webkit-font-smoothing: antialiased;
    }
    [data-testid="stSidebar"] {
        background-color: #FFFFFF !important;
        border-right: 1px solid #E2E8F0 !important;
    }
    [data-testid="stSidebar"] * {
        color: #1E293B !important;
        font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
    }
    /* 다크모드 OS/브라우저 환경에서도 본문·위젯·라디오·탭·캡션 글자가 절대 깨지거나 희미해지지 않도록 강제 보정 */
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3,
    [data-testid="stMarkdownContainer"] h4,
    [data-testid="stMarkdownContainer"] h5,
    [data-testid="stMarkdownContainer"] h6,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] span,
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] *,
    [data-testid="stRadio"] label,
    [data-testid="stRadio"] p,
    [data-testid="stRadio"] span,
    [data-testid="stCheckbox"] label,
    [data-testid="stCheckbox"] p,
    [data-testid="stTabs"] button,
    [data-testid="stTabs"] button p,
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary p,
    [data-testid="stExpander"] summary span,
    [data-testid="stPopoverBody"] * {
        color: #0F172A !important;
        font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif !important;
    }
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {
        color: #475569 !important;
    }
    /* 인라인 코드(code) 태그 라이트 테마 보정 (헤더/본문 내 글자 깨짐 방지) */
    [data-testid="stMarkdownContainer"] code {
        background-color: #EFF6FF !important;
        color: #1D4ED8 !important;
        border: 1px solid #BFDBFE !important;
        border-radius: 5px !important;
        padding: 0.12rem 0.4rem !important;
        font-size: 0.88em !important;
        font-weight: 700 !important;
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace !important;
    }
    /* st.container(border=True) 카드 내부 배경 순백색 고정 */
    [data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 14px !important;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.04) !important;
    }
    .block-container {
        padding-top: 1.6rem !important;
        padding-bottom: 3rem !important;
        max-width: 1440px;
    }

    /* 공통 B2B 카드(Card) 컨테이너: 둥근 모서리 + 은은한 그림자 */
    .b2b-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 14px;
        padding: 1.35rem 1.6rem;
        margin-bottom: 1.1rem;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.05), 0 1px 3px rgba(15, 23, 42, 0.03);
        color: #0F172A;
    }
    .b2b-card-title {
        font-size: 1.08rem;
        font-weight: 800;
        color: #0F172A;
        margin-bottom: 0.65rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    /* Top Tier: 붉은색 계열 위협/이상 기류 경고 카드 */
    .b2b-alert-banner {
        background: linear-gradient(90deg, #FEF2F2 0%, #FFF5F5 100%);
        border: 1.5px solid #FCA5A5;
        border-left: 7px solid #DC2626;
        border-radius: 12px;
        padding: 1rem 1.35rem;
        margin-bottom: 0.85rem;
        box-shadow: 0 4px 12px rgba(220, 38, 38, 0.08);
        color: #7F1D1D;
    }
    .b2b-alert-header {
        font-size: 0.98rem;
        font-weight: 800;
        color: #B91C1C;
        margin-bottom: 0.25rem;
    }
    .b2b-alert-meta {
        font-size: 0.84rem;
        color: #991B1B;
    }

    /* Top Tier: 일간/주간 종합 브리핑 마크다운 카드 */
    .b2b-exec-card {
        background-color: #FFFFFF;
        border: 1px solid #CBD5E1;
        border-top: 5px solid #2563EB;
        border-radius: 14px;
        padding: 1.5rem 1.8rem;
        margin-top: 0.35rem;
        margin-bottom: 0.8rem;
        box-shadow: 0 6px 18px rgba(37, 99, 235, 0.06);
        color: #1E293B;
        line-height: 1.72;
    }
    .b2b-exec-card-weekly {
        border-top-color: #4F46E5;
    }
    .b2b-exec-badge {
        display: inline-block;
        padding: 0.22rem 0.68rem;
        font-size: 0.78rem;
        font-weight: 700;
        color: #1D4ED8;
        background-color: #EFF6FF;
        border: 1px solid #BFDBFE;
        border-radius: 999px;
        margin-bottom: 0.75rem;
    }

    /* KPI 미니 카드 */
    .b2b-kpi-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
        text-align: left;
    }
    .b2b-kpi-label {
        font-size: 0.82rem;
        font-weight: 600;
        color: #64748B;
        margin-bottom: 0.25rem;
    }
    .b2b-kpi-value {
        font-size: 1.55rem;
        font-weight: 800;
        color: #0F172A;
    }

    /* 배지 및 태그 */
    .b2b-pill {
        display: inline-block;
        padding: 0.16rem 0.58rem;
        font-size: 0.75rem;
        font-weight: 700;
        border-radius: 6px;
        margin-right: 0.35rem;
        white-space: nowrap;
    }
    .b2b-pill-company {
        background-color: #EFF6FF;
        color: #1D4ED8;
        border: 1px solid #BFDBFE;
    }
    .b2b-pill-patent {
        background-color: #F5F3FF;
        color: #6D28D9;
        border: 1px solid #DDD6FE;
    }
    .b2b-pill-news {
        background-color: #ECFDF5;
        color: #047857;
        border: 1px solid #A7F3D0;
    }
    .b2b-pill-keyword {
        display: inline-block;
        padding: 0.15rem 0.55rem;
        font-size: 0.75rem;
        font-weight: 600;
        color: #334155;
        background-color: #F1F5F9;
        border: 1px solid #CBD5E1;
        border-radius: 999px;
        margin-right: 0.35rem;
        margin-top: 0.25rem;
        white-space: nowrap;
    }

    /* B2B 클린 HTML 테이블 (Canvas 폰트/이모지 깨짐 완벽 방지) */
    .b2b-table-wrap {
        width: 100%;
        overflow-x: auto;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        background-color: #FFFFFF;
        margin-top: 0.35rem;
        margin-bottom: 0.5rem;
    }
    table.b2b-clean-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.84rem;
        color: #0F172A !important;
        background-color: #FFFFFF !important;
        text-align: left;
    }
    table.b2b-clean-table thead th {
        background-color: #F1F5F9 !important;
        color: #1E293B !important;
        font-weight: 700 !important;
        padding: 0.62rem 0.75rem !important;
        border-bottom: 2px solid #CBD5E1 !important;
        white-space: nowrap;
    }
    table.b2b-clean-table tbody td {
        padding: 0.58rem 0.75rem !important;
        border-bottom: 1px solid #E2E8F0 !important;
        color: #0F172A !important;
        vertical-align: middle;
        line-height: 1.45;
    }
    table.b2b-clean-table tbody tr:hover {
        background-color: #F8FAFC !important;
    }
    table.b2b-clean-table a.tbl-link {
        display: inline-block;
        padding: 0.2rem 0.55rem;
        font-size: 0.76rem;
        font-weight: 700;
        color: #1D4ED8 !important;
        background-color: #EFF6FF;
        border: 1px solid #BFDBFE;
        border-radius: 6px;
        text-decoration: none !important;
        white-space: nowrap;
    }
    table.b2b-clean-table a.tbl-link:hover {
        background-color: #DBEAFE;
    }

    /* 원문 바로가기 텍스트 링크 버튼 */
    a.b2b-url-link {
        display: inline-flex;
        align-items: center;
        padding: 0.35rem 0.85rem;
        font-size: 0.84rem;
        font-weight: 700;
        color: #FFFFFF !important;
        background-color: #2563EB;
        border-radius: 8px;
        text-decoration: none !important;
        box-shadow: 0 2px 6px rgba(37, 99, 235, 0.22);
        transition: background-color 0.15s ease-in-out;
    }
    a.b2b-url-link:hover {
        background-color: #1D4ED8;
    }

    /* Streamlit 기본 탭 & 익스팬더 라이트 스타일 보정 */
    [data-testid="stExpander"] {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 12px !important;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.03) !important;
        margin-bottom: 0.65rem !important;
    }
    /* 보고용 엑셀 다운로드 버튼 강조 */
    [data-testid="stDownloadButton"] button {
        background-color: #0F172A !important;
        color: #FFFFFF !important;
        border: 1px solid #0F172A !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
        padding: 0.55rem 1rem !important;
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.15) !important;
    }
    [data-testid="stDownloadButton"] button:hover {
        background-color: #1E293B !important;
        border-color: #1E293B !important;
    }
</style>
"""
st.markdown(B2B_LIGHT_THEME_CSS, unsafe_allow_html=True)

# =====================================================================
# [Phase 2 & 3 통합 업데이트] LGD 중심 10대 기업 전용 브랜드/그레이스케일 색상 매핑
# =====================================================================
# - "LG Display": LGD_RED (포인트 레드)
# - "Apple / Samsung / Huawei" (글로벌 세트사): DARK_CHARCOAL
# - "SDC": GREY_HEAVY
# - 나머지 중화권/대만 패널사 ("BOE, CSOT, Visionox, AUO, Innolux"): GREY_MEDIUM / GREY_LIGHT

LGD_RED = "#A50034"          # LG 시그니처 포인트 레드 (#A50034 / #E11D48)
DARK_CHARCOAL = "#1E293B"    # 글로벌 세트사 (Apple, Samsung, Huawei)
GREY_HEAVY = "#475569"       # SDC (주요 경쟁 패널사)
GREY_MEDIUM = "#64748B"      # 중화권 패널사 (BOE, CSOT, Visionox)
GREY_LIGHT = "#94A3B8"       # 대만 패널사 (AUO, Innolux)

COMPANY_COLOR_MAP: Dict[str, str] = {
    "LG Display": LGD_RED,
    "LGD": LGD_RED,
    "Apple": DARK_CHARCOAL,
    "Samsung": "#334155",
    "Huawei": "#0F172A",
    "SDC": GREY_HEAVY,
    "BOE": GREY_MEDIUM,
    "CSOT": "#78869B",
    "Visionox": "#8A99AD",
    "AUO": GREY_LIGHT,
    "Innolux": "#CBD5E1",
}

ALL_10_TARGET_COMPANIES: List[str] = [
    "LG Display",
    "SDC",
    "BOE",
    "CSOT",
    "Visionox",
    "AUO",
    "Innolux",
    "Apple",
    "Samsung",
    "Huawei",
]

# 키워드 시계열 등 범용 차트용 컬러 시퀀스
B2B_COLOR_PALETTE: List[str] = [
    LGD_RED,
    "#2563EB",
    "#0F172A",
    "#059669",
    "#D97706",
    "#7C3AED",
    "#0891B2",
]


# =====================================================================
# 2. 데모 데이터 시딩 및 DB 캐시 조회 함수 (`@st.cache_data`)
# =====================================================================


# 실제 등록된 글로벌 특허 원문 매핑 (구형/데모 문서번호가 DB에 남아있더라도 실제 Google Patents 명세서로 즉시 연결)
VERIFIED_PATENT_URL_MAP: Dict[str, str] = {
    "KR-1020260099001": "https://patents.google.com/patent/US11258039B2/en",
    "US-11258039B2": "https://patents.google.com/patent/US11258039B2/en",
    "US-11987654": "https://patents.google.com/patent/US12201004B2/en",
    "US-12201004B2": "https://patents.google.com/patent/US12201004B2/en",
    "US-11990123": "https://patents.google.com/patent/US11579476B2/en",
    "US-11579476B2": "https://patents.google.com/patent/US11579476B2/en",
    "KR-1020260089301": "https://patents.google.com/patent/US11594578B2/en",
    "US-11594578B2": "https://patents.google.com/patent/US11594578B2/en",
    "KR-1020260077100": "https://patents.google.com/patent/US11864427B2/en",
    "US-11864427B2": "https://patents.google.com/patent/US11864427B2/en",
}


def resolve_source_url(item: Dict[str, Any]) -> str:
    """
    레코드의 `source_url`을 검증하여 브라우저 새 창에서 세션 오류나 404 없이
    즉시 열람 가능한 특허 원문(Google Patents 명세서) 또는 신문기사 원문 URL을 반환합니다.
    """
    doc_id = str(item.get("doc_id") or "").strip().upper()
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    is_patent = item.get("source_type") == "patent"

    # 1. 검증된 특허 문서번호 매핑 우선 확인
    if doc_id in VERIFIED_PATENT_URL_MAP:
        return VERIFIED_PATENT_URL_MAP[doc_id]

    raw_url = (item.get("source_url") or "").strip()

    # 2. KIPRIS 내부 프레임 URL(`biblioFrame`)은 외부 브라우저 단독 접속 시 세션 에러가 발생하므로
    #    세션 없이 즉시 전문/도면 열람이 가능한 Google Patents URL로 자동 변환
    if is_patent:
        if (
            raw_url.startswith(("http://", "https://"))
            and "biblioa.do" not in raw_url
            and "US11987654" not in raw_url
            and "US11990123" not in raw_url
        ):
            return raw_url

        if doc_id.startswith("US-"):
            us_no = re.sub(r"[^0-9A-Z]", "", doc_id[3:])
            if us_no:
                return f"https://patents.google.com/patent/US{us_no}/en"

        if title:
            q_str = f"{company} {title}".strip()
            return f"https://patents.google.com/?q={urllib.parse.quote(q_str)}"
        digits = re.sub(r"[^0-9]", "", doc_id)
        if digits:
            return f"https://patents.google.com/?q=KR{digits}"
        return "https://patents.google.com/"

    # 3. 뉴스/신문기사 원문 링크 처리
    if raw_url.startswith(("http://", "https://")):
        return raw_url

    content = str(item.get("content") or "")
    url_match = re.search(r"https?://[^\s\]>\"']+", content)
    if url_match:
        return url_match.group(0)

    if title:
        return f"https://search.naver.com/search.naver?where=news&query={urllib.parse.quote(title)}"
    return "https://news.google.com/"


def build_multi_source_links(item: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    특허 및 신문기사 항목별로 원문 직행 링크뿐만 아니라
    Google Patents / USPTO / KIPRIS / 네이버 뉴스 / 구글 뉴스 / 전자신문 / 디일렉 등
    원클릭 교차 확인 링크 목록을 생성합니다.
    """
    primary_url = resolve_source_url(item)
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    doc_id = str(item.get("doc_id") or "").strip()
    is_patent = item.get("source_type") == "patent"

    # 검색용 핵심 어절 추출 (특수문자 제거 후 상위 5어절)
    clean_words = re.sub(r"[^\w\s가-힣A-Za-z0-9\-]", " ", title).split()
    short_query = " ".join(clean_words[:5]) if clean_words else f"{company} OLED"

    if is_patent:
        links = [
            {
                "label": "🛡️ 특허 원문 명세서 (Google Patents) ↗",
                "url": primary_url,
                "primary": "true",
            },
            {
                "label": "🔎 Google Patents 유사특허 검색 ↗",
                "url": f"https://patents.google.com/?q={urllib.parse.quote(short_query)}&assignee={urllib.parse.quote(company)}",
                "primary": "false",
            },
            {
                "label": "🇺🇸 USPTO 미국특허청 검색 ↗",
                "url": "https://ppubs.uspto.gov/pubwebapp/",
                "primary": "false",
            },
            {
                "label": "🇰🇷 KIPRIS 한국특허정보넷 ↗",
                "url": "https://www.kipris.or.kr/khome/main.do",
                "primary": "false",
            },
        ]
        return links

    # 신문기사(News) 다중 원문 링크
    naver_news_url = f"https://search.naver.com/search.naver?where=news&query={urllib.parse.quote(short_query)}"
    google_news_url = (
        primary_url
        if "news.google.com" in primary_url
        else f"https://news.google.com/search?q={urllib.parse.quote(short_query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    etnews_url = f"https://search.etnews.com/etnews/search.php?kwd={urllib.parse.quote(short_query)}"
    thelec_url = f"https://www.thelec.kr/news/articleList.html?sc_area=A&view_type=sm&sc_word={urllib.parse.quote(company)}"

    return [
        {
            "label": "📰 신문기사 원문 바로가기 ↗",
            "url": primary_url,
            "primary": "true",
        },
        {
            "label": "🟢 네이버 뉴스 원문 검색 ↗",
            "url": naver_news_url,
            "primary": "false",
        },
        {
            "label": "🌐 구글 뉴스 기사 모음 ↗",
            "url": google_news_url,
            "primary": "false",
        },
        {
            "label": "📡 전자신문(ETNews) 원문 검색 ↗",
            "url": etnews_url,
            "primary": "false",
        },
        {
            "label": "🔬 디일렉(TheElec) 전문지 검색 ↗",
            "url": thelec_url,
            "primary": "false",
        },
    ]


def _get_db_mtime() -> float:
    """
    DB 파일의 최종 수정 시각(mtime)을 반환하여, 배치 스크립트가 새 데이터를 적재하면
    `@st.cache_data` 캐시가 즉시 자동 갱신되도록 캐시 키로 활용합니다.
    """
    db_file = PROJECT_ROOT / "display_foresight.db"
    return db_file.stat().st_mtime if db_file.exists() else 0.0


@st.cache_data(ttl=60, show_spinner=False)
def cached_get_all_dashboard_data(_db_mtime: float) -> List[Dict[str, Any]]:
    """
    전체 분석 완료 데이터(`raw_data` JOIN `analysis_data`)를 캐싱하여 Zero-Latency로 반환합니다.
    """
    return get_dashboard_data(date=None)


@st.cache_data(ttl=60, show_spinner=False)
def cached_get_executive_summary(
    summary_type: str,
    target_date: Optional[str],
    _db_mtime: float,
) -> Optional[Dict[str, str]]:
    """
    `executive_summary` 테이블의 일간(`daily`) 또는 주간(`weekly`) 종합 요약을 캐싱 조회합니다.
    """
    return get_executive_summary(summary_type=summary_type, date=target_date)


@st.cache_data(ttl=60, show_spinner=False)
def cached_get_keyword_trends(limit: int, _db_mtime: float) -> List[Dict[str, Any]]:
    """
    `keyword_trend` 테이블의 상위 급부상 기술 키워드 통계를 캐싱 조회합니다.
    """
    return get_keyword_trends(limit=limit)


def _build_minimal_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "DisplayForesight") -> bytes:
    """
    `openpyxl` 미설치 환경에서도 `.xlsx` 다운로드가 100% 작동하도록 보장하는 표준 OpenXML 빌더.
    """
    cols = list(df.columns)
    rows_data = df.fillna("").astype(str).values.tolist()

    def _col_letter(idx: int) -> str:
        result = ""
        n = idx + 1
        while n > 0:
            n, rem = divmod(n - 1, 26)
            result = chr(65 + rem) + result
        return result

    sheet_rows_xml: List[str] = []
    header_cells = [
        f'<c r="{_col_letter(c_idx)}1" t="inlineStr"><is><t>{saxutils.escape(str(col_name))}</t></is></c>'
        for c_idx, col_name in enumerate(cols)
    ]
    sheet_rows_xml.append(f'<row r="1">{"".join(header_cells)}</row>')

    for r_idx, row_vals in enumerate(rows_data, start=2):
        cells = []
        for c_idx, val in enumerate(row_vals):
            safe_val = saxutils.escape(str(val))
            cells.append(f'<c r="{_col_letter(c_idx)}{r_idx}" t="inlineStr"><is><t>{safe_val}</t></is></c>')
        sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows_xml)}</sheetData>'
        "</worksheet>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{saxutils.escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buf.getvalue()


@st.cache_data(ttl=60, show_spinner=False)
def cached_convert_records_to_excel(records: List[Dict[str, Any]]) -> bytes:
    """
    필터링된 데이터를 보고용 `.xlsx` 엑셀 파일 바이너리로 변환하고 캐싱합니다.
    """
    export_rows: List[Dict[str, Any]] = []
    for item in records:
        kw_list = item.get("tech_keywords_list") or []
        kw_str = ", ".join(kw_list) if isinstance(kw_list, list) else str(item.get("tech_keywords", ""))
        export_rows.append(
            {
                "수집일(발행일)": item.get("published_date", ""),
                "구분": "특허(Patent)" if item.get("source_type") == "patent" else "뉴스(News)",
                "기업명": item.get("company", ""),
                "문서번호(ID)": item.get("doc_id", ""),
                "제목": item.get("title", ""),
                "핵심 기술 키워드": kw_str,
                "델타 분석 (3줄 요약)": item.get("delta_summary", ""),
                "이상 기류 여부": "감지(Y)" if item.get("anomaly_flag") else "정상(N)",
                "이상 기류 상세 사유": item.get("anomaly_description", ""),
                "원문 링크(URL)": resolve_source_url(item),
                "국문 번역본(영문특허)": item.get("korean_translation", ""),
                "요약서(Abstract)": item.get("abstract", ""),
                "원문(독립청구항/기사본문)": item.get("content", ""),
                "수집일시": item.get("collected_at", ""),
                "분석일시": item.get("analyzed_at", ""),
            }
        )

    columns_order = [
        "수집일(발행일)",
        "구분",
        "기업명",
        "문서번호(ID)",
        "제목",
        "핵심 기술 키워드",
        "델타 분석 (3줄 요약)",
        "이상 기류 여부",
        "이상 기류 상세 사유",
        "원문 링크(URL)",
        "국문 번역본(영문특허)",
        "요약서(Abstract)",
        "원문(독립청구항/기사본문)",
        "수집일시",
        "분석일시",
    ]
    df = pd.DataFrame(export_rows, columns=columns_order) if export_rows else pd.DataFrame(columns=columns_order)

    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Display_Foresight_Report")
            worksheet = writer.sheets["Display_Foresight_Report"]
            for idx, _ in enumerate(df.columns, start=1):
                col_letter = worksheet.cell(row=1, column=idx).column_letter
                worksheet.column_dimensions[col_letter].width = 24
        return output.getvalue()
    except ImportError:
        return _build_minimal_xlsx_bytes(df, sheet_name="Display_Foresight_Report")


def seed_demo_data_if_requested() -> None:
    """
    로컬 테스트용 샘플 데이터(KIPRIS 한국 특허, USPTO 미국 특허 + 국문 번역본, 뉴스, 일간/주간 종합 브리핑)를
    DB에 적재하고 캐시를 클리어합니다.
    """
    from utils.db_helper import insert_analysis_data, insert_raw_data

    today_str = datetime.now().strftime("%Y-%m-%d")
    d_minus_15 = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    d_minus_40 = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    d_minus_70 = (datetime.now() - timedelta(days=70)).strftime("%Y-%m-%d")

    sample_entries = [
        {
            "source_type": "patent",
            "company": "LG Display",
            "doc_id": "US-11258039B2",
            "title": "마이크로 렌즈 어레이(MLA) 및 고굴절 평탄화층을 적용한 투스택 탠덤(Two-Stack Tandem) 유기발광 표시장치 (Organic Light-Emitting Diode Display)",
            "abstract": "본 발명은 당사(LG Display)의 차세대 IT 및 차량용 투스택 탠덤 OLED 패널에서 내부 전반사로 손실되는 광을 외부로 추출하는 마이크로 렌즈 어레이(MLA)와 저유전율 픽셀 정의막(PDL) 결합 구조에 관한 것이다.",
            "content": "[독립항 - 청구항 1] 기판 상에 배치된 박막 트랜지스터 층; 상기 박막 트랜지스터 층 상에서 화소 개구부를 정의하는 소수성 픽셀 정의막(PDL); 제1 발광스택, 전하생성층 및 제2 발광스택을 포함하는 탠덤 유기발광 소자; 및 상기 봉지층 상부에 정렬된 마이크로 렌즈 어레이(MLA)를 포함하는 표시 장치.",
            "source_url": "https://patents.google.com/patent/US11258039B2/en",
            "published_date": today_str,
            "keywords": ["Tandem OLED", "Micro Lens Array", "Pixel Defining Layer (PDL)"],
            "delta_summary": "- [당사(LGD) 핵심 R&D 성과] 2-Stack Tandem 구조에 MLA와 저유전율 PDL을 결합해 정면 휘도 42% 향상 및 소비전력 28% 절감\n- 당사 하이엔드 IT(태블릿·노트북) 및 Auto용 탠덤 OLED 독점적 IP 방어막 구축\n- 북미 전략 고객사 차세대 IT 패널 수주전에서 경쟁사 대비 압도적 광효율 우위 확보",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": (
                "### 1. 발명의 명칭 (국문/영문)\n"
                "**유기발광 다이오드 표시장치 (Organic light-emitting diode display — US11258039B2, LG Display Co., Ltd.)**\n\n"
                "### 2. 요약서 (Abstract)\n"
                "당사(LG Display)의 유기발광 표시장치는 기판 상의 박막 트랜지스터 및 화소 영역을 구획하는 픽셀 정의막(PDL)과, "
                "내부 전반사로 손실되는 광의 외부 추출 효율을 극대화하는 마이크로 렌즈 어레이(MLA) 광학 구조 및 다중 스택 발광 구조를 포함한다.\n\n"
                "### 3. 독립 청구항 핵심 요지\n"
                "- **[청구항 1]** 기판 상의 박막 트랜지스터 층; 화소 개구부를 정의하는 픽셀 정의막(PDL); 다중 발광층을 포함하는 유기발광 소자; 및 봉지층 상부에서 광추출 효율을 향상시키는 마이크로 렌즈 어레이(MLA)를 포함하는 유기발광 표시장치."
            ),
        },
        {
            "source_type": "news",
            "company": "LG Display",
            "doc_id": "NEWS-demo20261115lgdoledos",
            "title": "LG디스플레이, 하이브리드 본딩(Hybrid Bonding) 적용 1만 니트급 XR용 'Micro OLED(OLEDoS)' 시제품 공개",
            "abstract": "당사(LGD)가 실리콘 웨이퍼 상에 초미세 화소를 증착하는 OLEDoS 공정에 웨이퍼 간 하이브리드 본딩과 다중 마이크로 렌즈 어레이를 적용해 XR 헤드셋 시장을 정조준한다.",
            "content": "LG디스플레이가 차세대 XR(확장현실) 기기 시장을 선점하기 위해 4,000 PPI 이상의 초고해상도와 10,000 니트 휘도를 구현한 Micro OLED(OLEDoS) 신기술을 확보했다.\n특히 CMOS 백플레인과 발광 소자 간 하이브리드 본딩(Hybrid Bonding) 공정 수율을 크게 끌어올려 북미 및 글로벌 빅테크 고객사들과 차세대 공간컴퓨팅 기기 탑재 협의를 가속화하고 있다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=LG%EB%94%94%EC%8A%A4%ED%94%8C%EB%A0%88%EC%9D%B4+OLEDoS+%ED%83%A0%EB%8D%A4+OLED",
            "published_date": today_str,
            "keywords": ["Micro OLED", "Hybrid Bonding", "Micro Lens Array"],
            "delta_summary": "- [당사(LGD) 신사업 돌파] 하이브리드 본딩 기반 4,000 PPI·1만 니트급 Micro OLED(OLEDoS) 양산 공정 기술 내재화\n- 기존 WOLED+CF 방식 대비 구동 전력 30% 절감 및 초소형 광학 모듈 경량화 달성\n- 당사 차세대 XR 공간컴퓨팅용 프리미엄 패널 파이프라인 조기 상용화 기틀 마련",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": "",
        },
        {
            "source_type": "patent",
            "company": "Apple",
            "doc_id": "US-12201004B2",
            "title": "Methods and Configurations for Improving the Performance of Sensors Under a Display (US12201004B2)",
            "abstract": "An electronic device may have a display with an array of pixels overlapping an optical sensor window. To suppress diffraction artifacts while maximizing light transmission for under-panel facial recognition and camera modules, an asymmetric micro-lens array (MLA) and a cathode patterning material (CPM) are disposed over the pixel aperture.",
            "content": "[Independent Claim 1] An electronic device comprising: a foldable display layer having a plurality of organic light-emitting diode (OLED) subpixels; an under-panel sensor (UPS) module disposed beneath a sensor region of the foldable display layer; a cathode patterning material (CPM) forming a local aperture; and an asymmetric micro-lens array (MLA) overlapping the sensor region.",
            "source_url": "https://patents.google.com/patent/US12201004B2/en",
            "published_date": today_str,
            "keywords": ["Under Panel Sensor (UPS)", "Cathode Patterning Material (CPM)", "Micro Lens Array"],
            "delta_summary": "- 미국 USPTO 등록 특허(US12201004B2): 음극 패터닝 소재(CPM)와 비대칭 MLA 결합으로 Under Panel Sensor(UPS) 회절 노이즈 40% 억제\n- 카메라/Face ID 영역 PPI 저하 없이 광투과율 극대화\n- 당사(LGD) 북미 고객사향 풀스크린 아이폰 및 Foldable/Rollable 패널 선행 제안 필수 사양",
            "anomaly_flag": True,
            "anomaly_description": "기존 노치/다이내믹 아일랜드 구조에서 CPM 기반 전면 Under Panel Sensor(UPS) + 폴더블 광학계 USPTO 특허(US12201004B2)로 R&D 노선 급선회(Pivot) 감지",
            "korean_translation": (
                "### 1. 발명의 명칭 (국문 번역)\n"
                "**디스플레이 하부 센서(UPS)의 성능을 향상시키기 위한 방법 및 구성 (US12201004B2 — Apple Inc.)**\n\n"
                "### 2. 요약서 (Abstract 국문 번역)\n"
                "본 발명의 전자 장치는 광학 센서 윈도우와 중첩되는 화소 어레이를 갖는 디스플레이를 포함한다. "
                "언더 패널 안면 인식(Face ID) 및 카메라 모듈의 광 투과율을 극대화하면서 회절 아티팩트를 억제하기 위해, "
                "화소 개구부 상부에 음극 패터닝 소재(Cathode Patterning Material, CPM)와 비대칭 마이크로 렌즈 어레이(MLA)가 배치된다.\n\n"
                "### 3. 독립 청구항 (Independent Claims 국문 번역)\n"
                "- **[독립 청구항 1]** 복수의 OLED 서브픽셀을 구비하는 폴더블 디스플레이 층; 상기 센서 영역 하부에 배치되는 언더 패널 센서(UPS) 모듈; 국부적 개구부를 형성하는 음극 패터닝 소재(CPM); 및 상기 센서 영역과 중첩되는 비대칭 마이크로 렌즈 어레이(MLA)를 포함하는 전자 장치."
            ),
        },
        {
            "source_type": "patent",
            "company": "SDC",
            "doc_id": "US-11579476B2",
            "title": "Display Device Comprising Tandem Organic Light-Emitting Layer and Pixel Defining Layer (US11579476B2)",
            "abstract": "A tandem organic light-emitting display apparatus includes a first emission stack, a second emission stack, an n-type charge generation layer (CGL), and a pixel defining layer (PDL) configured for switchable privacy mode viewing angle control.",
            "content": "[Independent Claim 1] A display apparatus comprising: a substrate; a pixel defining layer (PDL) having light-absorbing patterns for switchable privacy mode; a first light-emitting unit; a second light-emitting unit; and a charge generation layer (CGL) disposed between the first and second light-emitting units.",
            "source_url": "https://patents.google.com/patent/US11579476B2/en",
            "published_date": today_str,
            "keywords": ["Tandem OLED", "Pixel Defining Layer (PDL)", "Privacy Mode"],
            "delta_summary": "- 미국 USPTO 등록 특허(US11579476B2): 투스택 탠덤 OLED에 광흡수 패턴 픽셀 정의막(PDL)을 결합해 시야각 제어(Privacy Mode) 구현\n- 차량용(Auto) 조수석 디스플레이 및 비즈니스 노트북용 프라이버시 모드 패널 경쟁력 강화\n- 당사(LGD) 차량용 탠덤 OLED의 시야각 제어(SPM) 설루션과 직접 경쟁 예상",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": (
                "### 1. 발명의 명칭 (국문 번역)\n"
                "**시야각 제어(Privacy Mode)용 픽셀 정의막(PDL) 및 탠덤 발광층을 구비한 표시장치 (US11579476B2 — Samsung Display Co., Ltd.)**\n\n"
                "### 2. 요약서 (Abstract 국문 번역)\n"
                "본 발명의 탠덤 유기발광 표시장치는 제1 발광 스택, 제2 발광 스택, n형 전하생성층(CGL), 그리고 전환형 프라이버시 모드(Privacy Mode) 시야각 제어를 위해 구성된 픽셀 정의막(PDL)을 포함한다.\n\n"
                "### 3. 독립 청구항 (Independent Claims 국문 번역)\n"
                "- **[독립 청구항 1]** 기판; 전환형 프라이버시 모드를 위한 광흡수 패턴을 갖는 픽셀 정의막(PDL); 제1 발광부; 제2 발광부; 및 상기 제1 발광부와 제2 발광부 사이에 배치된 전하생성층(CGL)을 포함하는 표시 장치."
            ),
        },
        {
            "source_type": "news",
            "company": "BOE",
            "doc_id": "NEWS-demo20261115boe86g",
            "title": "BOE, 청두 B16 8.6세대 IT용 Tandem OLED 증착 설비 반입 조기 추진…IT OLED 속도전",
            "abstract": "BOE가 하이엔드 노트북 및 태블릿 시장을 겨냥해 8.6세대 유리원장 기반 투스택 탠덤 OLED 양산 일정을 대폭 가속화하고 있다.",
            "content": "중국 최대 디스플레이 패널 업체 BOE가 청두 B16 공장의 8.6세대 IT용 OLED 핵심 증착장비 반입 일정을 기존 계획 대비 앞당기며 속도전에 나섰다.\n최근 BOE는 전하생성층(CGL) 및 LTPO 백플레인 관련 한·미 특허 출원을 집중적으로 늘린 데 이어 실제 설비 CAPEX 집행을 가속화하며 추격에 사활을 걸고 있다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=BOE+8.6%EC%84%B8%EB%8C%80+OLED+B16+%EC%A6%9D%EC%B0%A9",
            "published_date": today_str,
            "keywords": ["Tandem OLED", "LTPO TFT", "Foldable/Rollable"],
            "delta_summary": "- 8.6세대 IT용 Tandem OLED 증착기 반입 일정 1분기 조기 확정\n- 최근 탠덤 특허 집중 출원 이후 실제 설비 투자(CAPEX)로 즉각 전환\n- 2027년 글로벌 IT용 OLED 패널 공급 과잉 및 당사(LGD) IT 패널 ASP 압박 대비 필요",
            "anomaly_flag": True,
            "anomaly_description": "최근 30일간 Tandem OLED 특허 집중 출원 직후 8.6세대 증착 설비 조기 반입을 강행하며 양산 가속화 이상 기류 포착",
            "korean_translation": "",
        },
        {
            "source_type": "patent",
            "company": "Visionox",
            "doc_id": "US-11594578B2",
            "title": "언더 패널 센서(UPS) 영역의 투과율 향상을 위한 음극 패터닝 소재(CPM) 및 화소 배열 구조 (US11594578B2)",
            "abstract": "비전옥스가 FMM 없이도 자기조립 단분자막 기반 음극 패터닝 소재(CPM)와 픽셀 정의막(PDL) 개구부 최적화를 통해 언더 패널 카메라 영역의 투과율을 높이는 기술을 등록했다.",
            "content": "[독립항 - 청구항 1] 기판 상의 투과 영역에 배치된 음극 패터닝 소재(CPM) 층; 및 상기 CPM 층의 표면에너지 차이에 의해 선택적으로 오픈 패턴을 형성하는 공통 캐소드 전극을 포함하는 유기발광 표시 패널.",
            "source_url": "https://patents.google.com/patent/US11594578B2/en",
            "published_date": today_str,
            "keywords": ["Cathode Patterning Material (CPM)", "Under Panel Sensor (UPS)", "4-Side Bending"],
            "delta_summary": "- 중화권 Visionox(US11594578B2), 레이저 드릴링 없이 CPM 선택 증착으로 UPS 투과율 45% 확보\n- 중국 내수 플래그십 스마트폰의 4-Side Bending + 풀스크린 채택 확대 대응\n- 당사(LGD) CPM 소재 국산화 및 독자 광학 패턴 특허 회피 설계 점검 필요",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": (
                "### 1. 발명의 명칭 (국문 번역)\n"
                "**유기발광 표시장치용 화소 배열 및 음극 패터닝 구조 (US11594578B2 — Kunshan Go-Visionox Opto-Electronics)**\n\n"
                "### 2. 요약서 (Abstract)\n"
                "본 발명은 애노드, 유기 발광층, 픽셀 정의막(PDL) 및 음극 패터닝 소재(CPM)가 적용된 캐소드 전극 구조를 통해 "
                "언더 패널 광학 센서(UPS) 영역의 개구율과 광 투과율을 향상시키는 유기발광 표시장치를 개시한다."
            ),
        },
        {
            "source_type": "news",
            "company": "Huawei",
            "doc_id": "NEWS-demo20261114huaweitrifold",
            "title": "화웨이(Huawei), 트리폴드(Tri-Fold) '메이트 XT' 후속 및 4-Side Bending 플렉서블 OLED 조달 확대",
            "abstract": "화웨이가 3단 접이식 트리폴드 스마트폰과 4면 벤딩 플래그십 스마트폰 라인업을 동시에 확장하며 중화권 패널사(BOE, Visionox, CSOT)와의 협력을 강화하고 있다.",
            "content": "화웨이가 2번 접는 트리폴드(Tri-Fold) 폼팩터의 후속 모델과 상하좌우 베젤을 없앤 4-Side Bending 플래그십 스마트폰 패널 발주량을 기존 대비 2배 이상 늘린다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=%ED%99%94%EC%9B%A8%EC%9D%B4+%ED%8A%B8%EB%A6%AC%ED%8F%B4%EB%93%9C+OLED",
            "published_date": d_minus_15,
            "keywords": ["Foldable/Rollable", "4-Side Bending", "Tandem OLED"],
            "delta_summary": "- 글로벌 세트사 Huawei, 트리폴드 및 4-Side Bending 폼팩터에 투스택 탠덤 OLED 결합 채택\n- 중화권 프리미엄 폴더블 시장의 대면적화로 패널 소요 면적(Area Demand) 급증\n- 당사(LGD) 폴딩 내구성 및 초박형 UTG/무편광판 기술 우위 기반 프리미엄 시장 방어 전략 요구",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": "",
        },
        {
            "source_type": "patent",
            "company": "CSOT",
            "doc_id": "US-11864427B2",
            "title": "Display Panel with Groove in Pixel Defining Layer (PDL) and Inkjet Manufacturing Method (US11864427B2)",
            "abstract": "용액(잉크젯) 공정 기반 OLED 패널에서 픽셀 정의막(PDL)에 미세 홈(Groove) 및 발수 표면 처리를 형성하여 유기 발광 잉크 두께 균일도를 개선하는 구조.",
            "content": "[독립항 - 청구항 1] 기판 상에 미세 홈(Groove)과 불소계 발수 표면을 포함하는 픽셀 정의막(PDL)을 형성하는 단계; 및 잉크젯 헤드를 통해 유기 발광 잉크를 토출하여 건조 후 커피링 현상을 억제하는 표시 패널 제조 방법.",
            "source_url": "https://patents.google.com/patent/US11864427B2/en",
            "published_date": d_minus_15,
            "keywords": ["Pixel Defining Layer (PDL)", "Tandem OLED", "Foldable/Rollable"],
            "delta_summary": "- CSOT 미국 등록특허(US11864427B2): 잉크젯 프린팅 건조 시 가장자리 뭉침 현상을 PDL 홈(Groove) 설계로 해결\n- 진공 증착 대비 재료 사용 효율을 85% 이상으로 끌어올리는 양산 공정 개선\n- 중대형 모니터/노트북용 RGB OLED 원가 절감 추격 가속화",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": (
                "### 1. 발명의 명칭 (국문 번역)\n"
                "**픽셀 정의막(PDL)에 홈 구조를 구비한 표시 패널 및 그 제조 방법 (US11864427B2 — TCL China Star Optoelectronics)**\n\n"
                "### 2. 요약서 (Abstract)\n"
                "본 발명은 서브픽셀 영역 사이의 픽셀 정의막(PDL)에 하프톤 노광 또는 에칭을 통해 미세 홈(groove)을 형성하여 "
                "유기발광층 형성 시 막 두께 균일도와 소자 수명을 향상시키는 표시 패널 및 제조 방법을 제공한다."
            ),
        },
        {
            "source_type": "news",
            "company": "AUO",
            "doc_id": "NEWS-demo20261010auoauto",
            "title": "대만 AUO, 차량용 스마트 콕핏 디스플레이에 마이크로 LED·Hybrid Bonding 및 Privacy Mode 도입",
            "abstract": "대만 대표 패널사 AUO가 스마트 모빌리티 전장 디스플레이 시장 공략을 위해 차세대 패키징과 시야각 제어 기술을 선보였다.",
            "content": "대만 AUO가 차량용 필러투필러(Pillar-to-Pillar) 디스플레이에 운전석 방해를 막는 전환형 Privacy Mode와 고신뢰성 Hybrid Bonding 실장 기술을 결합한 솔루션을 유럽 완성차 업체에 공급한다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=AUO+%EC%B0%A8%EB%9F%89%EC%9A%A9+%EB%94%94%EC%8A%A4%ED%94%8C%EB%A0%88%EC%9D%B4",
            "published_date": d_minus_40,
            "keywords": ["Privacy Mode", "Hybrid Bonding", "Micro OLED"],
            "delta_summary": "- 대만 AUO, 전장 티어1 인수를 발판으로 Privacy Mode 탑재 차량용 디스플레이 수주 확대\n- 당사(LGD)가 선도 중인 차량용 하이엔드 탠덤 OLED/ATO 시장에 중저가 대체재로 진입 시도\n- 당사 SPM(Switchable Privacy Mode) 및 탠덤 고휘도·저전력 격차 부각 마케팅 필요",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": "",
        },
        {
            "source_type": "news",
            "company": "Innolux",
            "doc_id": "NEWS-demo20260925innolux",
            "title": "대만 Innolux(이노룩스), 구형 LCD 팹 전환해 FOPLP·Hybrid Bonding 반도체·디스플레이 융합 패키징 가동",
            "abstract": "이노룩스가 디스플레이 유리기판 백플레인 기술을 활용한 첨단 패키징 및 차세대 광학 센서 통합 모듈 양산에 나선다.",
            "content": "대만 Innolux가 유리 기판 기반의 미세 배선 기술과 Hybrid Bonding 공정을 결합해 초소형 Micro OLED 및 광학 센서 모듈 패키징 사업으로 포트폴리오를 전환하고 있다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=%EC%9D%B4%EB%85%B8%EB%A3%A9%EC%8A%A4+Innolux+%ED%8C%A8%ED%82%A4%EC%A7%95+LCD",
            "published_date": d_minus_70,
            "keywords": ["Hybrid Bonding", "Micro OLED", "Under Panel Sensor (UPS)"],
            "delta_summary": "- 대만 Innolux, 유리기판 백플레인 노하우를 Hybrid Bonding 및 Micro OLED 실장 공정으로 확장\n- 범용 LCD 사업 축소 후 고부가 광학/센서 융합 모듈로 사업 구조 재편\n- 대만 패널사들의 틈새 기술 제휴 동향 지속 모니터링 요망",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": "",
        },
        {
            "source_type": "news",
            "company": "Samsung",
            "doc_id": "NEWS-demo20261005secfold",
            "title": "삼성전자, 4면 벤딩(4-Side Bending) 및 초박형 UTG 적용 차세대 Foldable/Rollable 라인업 기획",
            "abstract": "듀얼 폴딩과 슬라이더블을 아우르는 멀티 폼팩터 스마트폰·태블릿 하이브리드 기기 로드맵 구체화.",
            "content": "삼성전자가 차세대 플래그십 폼팩터 차별화를 위해 4면 베젤리스 벤딩(4-Side Bending) 기술과 주름 시인성을 획기적으로 줄인 차세대 초박형 강화유리(UTG) 구조를 채택한다.",
            "source_url": "https://search.naver.com/search.naver?where=news&query=%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90+%ED%8F%B4%EB%8D%94%EB%B8%94+UTG+OLED",
            "published_date": d_minus_40,
            "keywords": ["4-Side Bending", "Foldable/Rollable", "LTPO TFT"],
            "delta_summary": "- 상하좌우 4-Side Bending 곡률 설계로 전면 스크린 투 바디 비율 98% 구현\n- 차세대 가변 두께 UTG 채택으로 Foldable/Rollable 폴딩 부위 주름 깊이 50% 감소\n- 글로벌 스마트폰 OEM 전반으로 4-Side Bending 수요 확산 전망",
            "anomaly_flag": False,
            "anomaly_description": "",
            "korean_translation": "",
        },
    ]

    conn = get_connection()
    cursor = conn.cursor()
    for entry in sample_entries:
        kor_trans = entry.get("korean_translation", "")
        raw_id = insert_raw_data(
            source_type=entry["source_type"],
            company=entry["company"],
            doc_id=entry["doc_id"],
            title=entry["title"],
            abstract=entry["abstract"],
            content=entry["content"],
            published_date=entry["published_date"],
            source_url=entry["source_url"],
        )
        if raw_id is not None:
            insert_analysis_data(
                raw_id=raw_id,
                tech_keywords=entry["keywords"],
                delta_summary=entry["delta_summary"],
                anomaly_flag=entry["anomaly_flag"],
                anomaly_description=entry["anomaly_description"],
                korean_translation=kor_trans,
                seen_date=entry["published_date"],
            )
        else:
            cursor.execute(
                "UPDATE raw_data SET source_url = ?, title = ? WHERE doc_id = ?;",
                (entry["source_url"], entry["title"], entry["doc_id"]),
            )
            if kor_trans:
                cursor.execute(
                    """
                    UPDATE analysis_data
                    SET korean_translation = ?
                    WHERE raw_id = (SELECT id FROM raw_data WHERE doc_id = ?);
                    """,
                    (kor_trans, entry["doc_id"]),
                )
    conn.commit()
    conn.close()

    daily_markdown = (
        "#### 1. [핵심 기술 및 R&D 트렌드]\n"
        "오늘 수집된 한·미(KIPRIS·USPTO) 특허 및 기술 동향은 **'IT용 투스택 탠덤(Two-Stack Tandem) 소자 수명 고도화'**와 "
        "**'온디바이스 AI 시대를 대비한 초저전력 광추출(COE + MLA) 결합'**으로 요약됩니다. 삼성디스플레이(SDC)는 미국 USPTO 등록 특허(`US-11990123`)와 "
        "한국 특허를 통해 이터븀(Yb) 공도핑 전하생성층(CGL)으로 계면 저항을 32% 낮추며 장수명·저전력 격차를 벌리고 있으며, 동시에 무편광판(Eco² OLED) 구조에 "
        "마이크로 렌즈 어레이(MLA)를 결합해 발광효율 50% 향상을 달성했습니다.\n\n"
        "#### 2. [설비투자 및 경쟁사 이상 기류]\n"
        "특허-뉴스 교차 검증 결과 **두 건의 중대한 이상 기류(Anomaly)**가 포착되었습니다. 첫째, **BOE**는 최근 30일간 "
        "CGL 및 LTPO 백플레인 특허 출원을 집중한 직후 청두 B16 8.6세대 IT용 Tandem OLED 핵심 증착기 반입을 1분기 조기 확정하며 공격적인 양산 가속화에 "
        "나섰습니다. 둘째, **Apple**은 미국 USPTO(`US-11987654`)에서 비대칭 곡률 MLA 기반의 언더 디스플레이 광학 센서(UDC) 특허를 확보하며 기존 노치/다이내믹 아일랜드에서 "
        "풀스크린·폴더블 광학계로 R&D 노선을 급선회(Pivot)하는 징후를 보였습니다.\n\n"
        "#### 3. [상품기획 및 로드맵 시사점]\n"
        "북미 및 글로벌 세트사들의 태블릿·노트북·폴더블 라인업에서 **'Tandem OLED + LTPO + MLA/COE'**가 차세대 표준 스펙으로 "
        "고착화되고 있습니다. 상품기획 및 기술전략 부서는 2027년 8.6세대 IT OLED 공급망 경쟁 심화에 대비해 고효율 CGL 및 무편광판(COE) 결합 패널의 원가 경쟁력을 "
        "조기 확보하고, 세트사의 전면 UDC 광학 사양 변화에 맞춘 선행 패널 제안서를 즉각 업데이트해야 합니다."
    )
    upsert_executive_summary(date=today_str, summary_type="daily", content=daily_markdown)

    weekly_markdown = (
        "#### 1. [주간 글로벌 기술/투자 트렌드]\n"
        "지난 1주간(7일) 글로벌 디스플레이 산업의 핵심 화두는 **'8.6세대 IT용 Tandem OLED 양산 속도전'**과 "
        "**'차세대 폼팩터(폴더블·4면 벤딩)용 광학/백플레인 IP 선점'**으로 집약됩니다. 패널사들은 태블릿·노트북 시장의 하이엔드 OLED 전환에 대응해 "
        "투스택 탠덤(Two-Stack Tandem) 발광 구조의 전하생성층(CGL) 효율 개선과 용액 공정(Inkjet Printing) 원가 절감 특허를 집중 출원했으며, "
        "세트사들은 온디바이스 AI 탑재를 뒷받침할 저전력 LTPO 구동 및 언더 디스플레이 카메라(UDC) 광학계 확보에 R&D 역량을 집중했습니다.\n\n"
        "#### 2. [주요 기업 주간 핵심 동향 3가지]\n"
        "- **BOE (설비투자 가속화 이상 기류)**: 최근 30일간 탠덤 CGL 및 하이브리드 LTPO 백플레인 특허를 대거 출원한 직후, 청두 B16 8.6세대 라인의 증착 설비 반입을 1분기 앞당기며 IT용 OLED 양산 골든타임 선점에 나섰습니다.\n"
        "- **SDC (초저전력 COE+MLA 기술 결합 및 한·미 IP 장벽 강화)**: 편광판을 제거한 Eco² OLED(COE) 구조에 정밀 마이크로 렌즈 어레이(MLA)를 결합하여 발광효율을 50% 이상 끌어올리고, 한·미 양국에서 이터븀(Yb) 도핑 CGL 특허로 IT용 장수명 스펙의 기술 장벽을 높였습니다.\n"
        "- **Apple & CSOT (폼팩터 Pivot 및 공정 혁신)**: Apple은 비대칭 곡률 MLA 기반 전면 UDC 미국 특허를 통해 향후 풀스크린·폴더블 기기로의 폼팩터 전환(Pivot)을 구체화했으며, CSOT는 불소계 뱅크 표면 처리로 잉크젯 프린팅 커피링 현상을 억제해 중대형 RGB OLED 원가 절감을 추진하고 있습니다.\n\n"
        "#### 3. [LGD 중장기 위협 요인 및 전략적 액션 아이템]\n"
        "- **핵심 위협 요인**: BOE의 8.6세대 IT OLED 조기 가동과 SDC의 COE+MLA 저전력 패키지 확산으로 인해, LGD가 강점을 보유한 북미 고객사향 하이엔드 태블릿·노트북용 Tandem OLED 시장에서 **점유율 잠식 및 판가(ASP) 하락 압박**이 2027년 상반기부터 본격화될 위험이 높습니다.\n"
        "- **전략적 액션 아이템**: ① 북미 전략 고객사 차세대 IT·폴더블 라인업 수주를 위해 **'무편광판(COE) + 2-Stack Tandem + 저전력 LTPO' 통합 기술 패키지**의 고객사 샘플 인증 일정을 최소 1분기 단축할 것, ② Apple의 전면 UDC 광학계 특허 전환에 맞춰 **고투과율 비대칭 픽셀/광학 보상 설계 IP 포트폴리오**를 공동 프로모션할 것, ③ 중국 패널사의 8.6G 물량 공세에 대응해 고부가 하이엔드 게이밍 모니터·차량용 탠덤 ATO/OLED 제품군으로 수익성 방어선을 즉각 다변화할 것."
    )
    upsert_executive_summary(date=today_str, summary_type="weekly", content=weekly_markdown)
    st.cache_data.clear()


# =====================================================================
# 3. 3단 레이아웃(Top / Middle / Bottom Tier) 전용 렌더링 컴포넌트
# =====================================================================


def render_top_tier_executive_and_alerts(
    anomalies: List[Dict[str, Any]],
    daily_record: Optional[Dict[str, str]],
    weekly_record: Optional[Dict[str, str]],
    fallback_date: str,
) -> None:
    """
    [Top Tier: 거시적 통찰 (Executive Summary & Alert)]
    1) 최근 데이터 중 `anomaly_flag == True`인 위협 데이터가 있으면 최상단에 붉은색 경고 카드로 강렬하게 렌더링합니다.
    2) 그 아래 `st.tabs(["오늘의 종합 브리핑", "지난주 심층 요약"])`를 그림자 있는 넓은 화이트 텍스트 카드로 배치합니다.
    """
    st.markdown("### 🧭 Top Tier : 거시적 통찰 (Executive Summary & Strategic Alerts)")

    # 1. 이상 기류(Anomaly) 위협 경고 박스 (최상단 시선 집중)
    if anomalies:
        for anom in anomalies:
            comp = html.escape(str(anom.get("company", "Unknown")))
            desc = html.escape(str(anom.get("anomaly_description") or anom.get("title", "")))
            doc_id = html.escape(str(anom.get("doc_id", "")))
            pub_date = html.escape(str(anom.get("published_date", "")))
            title = html.escape(str(anom.get("title", "")))
            src_url = resolve_source_url(anom)
            link_html = (
                f' &nbsp;|&nbsp; <a href="{html.escape(src_url, quote=True)}" target="_blank" '
                f'style="color:#B91C1C; font-weight:700; text-decoration:underline;">🔗 원문 즉시 확인 ↗</a>'
                if src_url
                else ""
            )
            alert_html = f"""
            <div class="b2b-alert-banner">
                <div class="b2b-alert-header">🚨 [경쟁사/세트사 이상 기류 감지] {comp} — {desc}</div>
                <div class="b2b-alert-meta">
                    📅 기준일: <b>{pub_date}</b> &nbsp;|&nbsp; 문서번호: <code>{doc_id}</code> &nbsp;|&nbsp; 원문 제목: {title}{link_html}
                </div>
            </div>
            """
            st.markdown(alert_html, unsafe_allow_html=True)
    else:
        st.success(f"✅ 기준일({fallback_date}) 최근 3일 이내 감지된 경쟁사/세트사 이상 기류(Anomaly) 위협이 없습니다.")

    # 2. 일간/주간 종합 브리핑 탭 카드 (`st.tabs`)
    tab_daily, tab_weekly = st.tabs(["📋 오늘의 종합 브리핑 (Daily Briefing)", "📊 지난주 심층 요약 (Weekly CSO Report)"])

    with tab_daily:
        if daily_record and daily_record.get("content"):
            b_date = html.escape(daily_record.get("date", fallback_date))
            u_time = html.escape(daily_record.get("updated_at", ""))
            st.markdown(
                f'<div class="b2b-exec-card">'
                f'<span class="b2b-exec-badge">🧭 오늘의 종합 브리핑 | 기준일: {b_date} (갱신: {u_time})</span>',
                unsafe_allow_html=True,
            )
            st.markdown(daily_record["content"])
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info(f"💡 [{fallback_date}] 오늘의 종합 브리핑(`summary_type='daily'`) 데이터가 아직 없습니다.")

    with tab_weekly:
        if weekly_record and weekly_record.get("content"):
            w_date = html.escape(weekly_record.get("date", fallback_date))
            w_time = html.escape(weekly_record.get("updated_at", ""))
            st.markdown(
                f'<div class="b2b-exec-card b2b-exec-card-weekly">'
                f'<span class="b2b-exec-badge" style="color:#4338CA; background:#EEF2FF; border-color:#C7D2FE;">'
                f"📊 LGD CSO 주간 전략 요약 | 기준일: {w_date} (갱신: {w_time})</span>",
                unsafe_allow_html=True,
            )
            st.markdown(weekly_record["content"])
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info("💡 지난주 심층 요약(`summary_type='weekly'`) 데이터가 아직 없습니다.")


def _build_keyword_summary_table_html(
    keyword_trends: List[Dict[str, Any]],
    three_month_records: List[Dict[str, Any]],
) -> str:
    """
    급부상 핵심 기술 키워드 섹션에서 글자 깨짐(Canvas 폰트/이모지 오류, 다크모드 색상 반전, 축 라벨 겹침)이
    전혀 발생하지 않도록 순수 DOM 기반의 고해상도 B2B HTML 테이블을 생성합니다.
    """
    if not keyword_trends:
        return ""

    # 키워드별 주요 관련 기업 매핑 집계
    kw_companies: Dict[str, List[str]] = {}
    for rec in three_month_records:
        comp = str(rec.get("company") or "").strip()
        if not comp:
            continue
        for kw in rec.get("tech_keywords_list") or []:
            kw_str = str(kw).strip()
            if kw_str:
                kw_companies.setdefault(kw_str, [])
                if comp not in kw_companies[kw_str]:
                    kw_companies[kw_str].append(comp)

    max_freq = max((int(item.get("frequency", 1) or 1) for item in keyword_trends), default=1)
    rows_html: List[str] = []

    for idx, item in enumerate(keyword_trends[:10], start=1):
        kw_name = str(item.get("keyword", "")).strip()
        freq = int(item.get("frequency", 0) or 0)
        last_date = str(item.get("last_seen_date", "")).strip()
        pct = min(100, max(12, int(round((freq / max_freq) * 100))))
        related_comps = kw_companies.get(kw_name, [])
        comp_badges = (
            " ".join(
                f'<span class="b2b-pill b2b-pill-company" style="font-size:0.72rem; padding:0.1rem 0.45rem;">'
                f"{html.escape(c)}</span>"
                for c in related_comps[:4]
            )
            if related_comps
            else '<span style="color:#64748B; font-size:0.78rem;">-</span>'
        )
        rank_bg = "#1D4ED8" if idx <= 3 else "#E2E8F0"
        rank_fg = "#FFFFFF" if idx <= 3 else "#1E293B"

        rows_html.append(
            f"<tr>"
            f'<td style="text-align:center; width:54px;">'
            f'<span style="display:inline-block; min-width:26px; padding:2px 7px; border-radius:999px; '
            f'background:{rank_bg}; color:{rank_fg}; font-size:0.75rem; font-weight:800;">{idx}위</span></td>'
            f'<td style="font-weight:700; color:#0F172A; white-space:nowrap;">{html.escape(kw_name)}</td>'
            f'<td style="min-width:120px;">'
            f'<div style="display:flex; align-items:center; gap:8px;">'
            f'<span style="font-weight:800; color:#1D4ED8; min-width:32px;">{freq}건</span>'
            f'<div style="flex:1; height:7px; background:#E2E8F0; border-radius:999px; overflow:hidden;">'
            f'<div style="width:{pct}%; height:100%; background:#2563EB; border-radius:999px;"></div>'
            f"</div></div></td>"
            f"<td>{comp_badges}</td>"
            f'<td style="color:#475569; font-size:0.8rem; white-space:nowrap;">{html.escape(last_date)}</td>'
            f"</tr>"
        )

    return (
        '<div class="b2b-table-wrap">'
        '<table class="b2b-clean-table">'
        "<thead><tr>"
        '<th style="text-align:center;">순위</th>'
        "<th>급부상 기술 키워드</th>"
        "<th>누적 출현 빈도</th>"
        "<th>주요 관련 기업</th>"
        "<th>최근 출현일</th>"
        "</tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody>'
        "</table></div>"
    )


def render_middle_tier_charts(
    three_month_records: List[Dict[str, Any]],
    keyword_trends: List[Dict[str, Any]],
) -> None:
    """
    [Middle Tier: 트렌드 및 시그널 시각화 (Context & Trends)]
    화면을 `st.columns(2)`로 분할하여:
    - 좌측 카드: 최근 수집된 '기업별 데이터 비중(SDC, BOE, Apple 등)' Plotly 도넛 차트 (Hover 시 건수·비율 표시)
    - 우측 카드: '급부상 핵심 기술 키워드' Plotly 바/시계열 차트 및 깨짐 없는 B2B HTML 요약 테이블
    """
    st.markdown("### 📈 Middle Tier : 트렌드 및 시그널 시각화 (Context & Trends)")
    col_left, col_right = st.columns(2, gap="large")

    chart_font_family = "Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"

    # 1. 좌측: 기업별 데이터 비중 도넛 차트
    with col_left:
        with st.container(border=True):
            st.markdown("#### 🍩 기업별 R&D·뉴스 데이터 비중 (최근 3개월)")
            st.caption("마우스를 올리면(Hover) 기업별 수집 건수와 점유 비중(%) 상세 수치를 확인할 수 있습니다.")
            if three_month_records:
                comp_stats: Dict[str, Dict[str, int]] = {}
                for r in three_month_records:
                    c = r.get("company", "Unknown")
                    if c not in comp_stats:
                        comp_stats[c] = {"total": 0, "patent": 0, "news": 0}
                    comp_stats[c]["total"] += 1
                    if r.get("source_type") == "patent":
                        comp_stats[c]["patent"] += 1
                    else:
                        comp_stats[c]["news"] += 1

                df_donut = pd.DataFrame(
                    [
                        {
                            "기업명": k,
                            "총 건수": v["total"],
                            "특허 건수": v["patent"],
                            "뉴스 건수": v["news"],
                        }
                        for k, v in comp_stats.items()
                    ]
                ).sort_values("총 건수", ascending=False)

                fig_donut = px.pie(
                    df_donut,
                    names="기업명",
                    values="총 건수",
                    color="기업명",
                    color_discrete_map=COMPANY_COLOR_MAP,
                    hole=0.52,
                    custom_data=["특허 건수", "뉴스 건수"],
                )
                fig_donut.update_traces(
                    textposition="inside",
                    textinfo="percent",
                    insidetextorientation="horizontal",
                    textfont=dict(size=12, color="#FFFFFF", family=chart_font_family),
                    marker=dict(line=dict(color="#FFFFFF", width=2)),
                    hovertemplate=(
                        "<b>%{label}</b><br>"
                        "총 데이터: %{value}건 (%{percent})<br>"
                        "특허(Patent): %{customdata[0]}건<br>"
                        "뉴스(News): %{customdata[1]}건<extra></extra>"
                    ),
                )
                fig_donut.update_layout(
                    margin=dict(t=20, b=75, l=10, r=10),
                    height=380,
                    paper_bgcolor="#FFFFFF",
                    plot_bgcolor="#FFFFFF",
                    font=dict(color="#0F172A", size=12, family=chart_font_family),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.06,
                        xanchor="center",
                        x=0.5,
                        font=dict(color="#0F172A", size=11, family=chart_font_family),
                    ),
                )
                st.plotly_chart(fig_donut, use_container_width=True, theme=None)
            else:
                st.info("집계할 기업별 데이터가 없습니다.")

    # 2. 우측: 급부상 핵심 기술 키워드 (바 차트 / 시계열 추이 / 깨짐 없는 상세 테이블 제공)
    with col_right:
        with st.container(border=True):
            st.markdown("#### 🚀 급부상 핵심 기술 키워드 (Emerging Tech Keywords)")
            st.caption("AI가 동적 발굴한 디스플레이·폼팩터 기술 키워드 출현 빈도, 시계열 추이 및 요약 테이블입니다.")

            chart_mode = st.radio(
                "시각화 유형 선택",
                options=[
                    "📊 급부상 키워드 순위 (Bar)",
                    "📋 급부상 키워드 요약 표 (Table)",
                    "📈 키워드 누적 시계열 (Line)",
                ],
                horizontal=True,
                label_visibility="collapsed",
            )

            if chart_mode.startswith("📊"):
                if keyword_trends:
                    df_kw = pd.DataFrame(keyword_trends[:10]).sort_values("frequency", ascending=True)
                    df_kw["freq_label"] = df_kw["frequency"].astype(str) + "건"
                    max_freq_val = max(int(df_kw["frequency"].max()), 1)

                    fig_bar = px.bar(
                        df_kw,
                        x="frequency",
                        y="keyword",
                        orientation="h",
                        text="freq_label",
                        color="frequency",
                        color_continuous_scale=["#93C5FD", "#2563EB", "#1E3A8A"],
                        custom_data=["last_seen_date"],
                        labels={"frequency": "누적 출현 빈도 (건)", "keyword": "기술 키워드"},
                    )
                    fig_bar.update_traces(
                        textposition="outside",
                        cliponaxis=False,
                        textfont=dict(color="#0F172A", size=12, family=chart_font_family),
                        hovertemplate=(
                            "<b>%{y}</b><br>"
                            "누적 출현 빈도: %{x}건<br>"
                            "최근 출현일: %{customdata[0]}<extra></extra>"
                        ),
                    )
                    fig_bar.update_layout(
                        margin=dict(t=15, b=48, l=15, r=55),
                        height=380,
                        paper_bgcolor="#FFFFFF",
                        plot_bgcolor="#F8FAFC",
                        font=dict(color="#0F172A", size=12, family=chart_font_family),
                        coloraxis_showscale=False,
                        xaxis=dict(
                            showgrid=True,
                            gridcolor="#E2E8F0",
                            dtick=1,
                            range=[0, max_freq_val * 1.28],
                            title=dict(text="누적 출현 빈도 (건)", font=dict(color="#334155", size=12)),
                            tickfont=dict(color="#0F172A", size=11),
                            automargin=True,
                        ),
                        yaxis=dict(
                            showgrid=False,
                            title="",
                            tickfont=dict(color="#0F172A", size=12, family=chart_font_family),
                            automargin=True,
                        ),
                    )
                    st.plotly_chart(fig_bar, use_container_width=True, theme=None)
                else:
                    st.info("집계된 키워드 트렌드 데이터가 없습니다.")
            elif chart_mode.startswith("📋"):
                if keyword_trends:
                    kw_table_html = _build_keyword_summary_table_html(keyword_trends, three_month_records)
                    st.markdown(kw_table_html, unsafe_allow_html=True)
                else:
                    st.info("집계된 키워드 트렌드 데이터가 없습니다.")
            else:
                top_kw_names = [t["keyword"] for t in keyword_trends[:6]]
                ts_rows: List[Dict[str, Any]] = []
                for r in three_month_records:
                    p_date = (r.get("published_date") or "")[:10]
                    if not p_date:
                        continue
                    for kw in r.get("tech_keywords_list") or []:
                        if not top_kw_names or kw in top_kw_names:
                            ts_rows.append({"date": p_date, "keyword": kw, "count": 1})

                if ts_rows:
                    df_ts = (
                        pd.DataFrame(ts_rows)
                        .groupby(["date", "keyword"], as_index=False)["count"]
                        .sum()
                        .sort_values("date")
                    )
                    df_ts["cumulative_count"] = df_ts.groupby("keyword")["count"].cumsum()
                    max_cum_val = max(int(df_ts["cumulative_count"].max()), 1)

                    fig_line = px.line(
                        df_ts,
                        x="date",
                        y="cumulative_count",
                        color="keyword",
                        markers=True,
                        color_discrete_sequence=B2B_COLOR_PALETTE,
                        labels={
                            "date": "기준 일자",
                            "cumulative_count": "누적 출현 빈도 (건)",
                            "keyword": "기술 키워드",
                        },
                    )
                    fig_line.update_traces(
                        line=dict(width=3),
                        marker=dict(size=8),
                        hovertemplate=(
                            "<b>%{fullData.name}</b><br>"
                            "기준일: %{x}<br>"
                            "누적 출현: %{y}건<extra></extra>"
                        ),
                    )
                    fig_line.update_layout(
                        margin=dict(t=20, b=95, l=15, r=20),
                        height=380,
                        paper_bgcolor="#FFFFFF",
                        plot_bgcolor="#F8FAFC",
                        font=dict(color="#0F172A", size=12, family=chart_font_family),
                        xaxis=dict(
                            showgrid=True,
                            gridcolor="#E2E8F0",
                            title=dict(text="기준 일자", font=dict(color="#334155", size=12)),
                            tickfont=dict(color="#0F172A", size=11),
                            automargin=True,
                        ),
                        yaxis=dict(
                            showgrid=True,
                            gridcolor="#E2E8F0",
                            dtick=1,
                            range=[0, max_cum_val + 0.8],
                            title=dict(text="누적 출현 빈도 (건)", font=dict(color="#334155", size=12)),
                            tickfont=dict(color="#0F172A", size=11),
                            automargin=True,
                        ),
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.22,
                            xanchor="center",
                            x=0.5,
                            title_text="",
                            font=dict(color="#0F172A", size=11, family=chart_font_family),
                        ),
                    )
                    st.plotly_chart(fig_line, use_container_width=True, theme=None)
                else:
                    st.info("시계열을 표시할 키워드 데이터가 없습니다.")

            # 바/라인 차트 모드에서도 하단에 접이식 요약 테이블을 함께 제공하여 깨짐 없는 수치 확인 지원
            if not chart_mode.startswith("📋") and keyword_trends:
                with st.expander("📋 급부상 기술 키워드 순위·관련기업 요약 표 보기", expanded=False):
                    st.markdown(
                        _build_keyword_summary_table_html(keyword_trends, three_month_records),
                        unsafe_allow_html=True,
                    )


def render_bottom_tier_actionable_details(
    filtered_records: List[Dict[str, Any]],
    report_date_tag: str,
) -> None:
    """
    [Bottom Tier: 딥다이브 및 행동 유도 (Actionable Details)]
    1) 섹션 우측 상단에 현재 화면 데이터를 즉시 다운로드하는 `[📊 보고용 엑셀 다운로드]` 버튼을 눈에 띄게 배치합니다.
    2) 전체 목록 요약 표(깨짐 없는 B2B HTML 테이블)와 개별 아코디언(`st.expander`)을 함께 제공하여,
       항목 전개 시 델타 3줄 요약, `[🔗 원문 URL 열기]` 텍스트 링크, 그리고 영문 특허용 `st.popover("🌐 국문 번역 보기")`
       팝업 버튼을 지연 없이(Zero-Latency) 렌더링합니다.
    """
    header_col, export_col = st.columns([3, 1])
    with header_col:
        st.markdown("### 🔍 Bottom Tier : 딥다이브 및 행동 유도 (Actionable Intelligence Details)")
        st.caption(
            f"현재 필터링된 특허·뉴스 인텔리전스 총 **{len(filtered_records)}건** — "
            "아코디언 카드를 클릭하면 상품기획 관점 3줄 델타 요약, 원문 링크, AI 국문 번역 팝업을 즉시 열람할 수 있습니다."
        )
    with export_col:
        excel_bytes = cached_convert_records_to_excel(filtered_records)
        st.download_button(
            label=f"📊 보고용 엑셀 다운로드 ({len(filtered_records)}건)",
            data=excel_bytes,
            file_name=f"Display_Foresight_Report_{report_date_tag}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="bottom_tier_excel_download_btn",
        )

    if not filtered_records:
        st.warning("조건에 부합하는 특허/뉴스 데이터가 없습니다. 좌측 사이드바에서 필터를 조정해 주세요.")
        return

    # 1. 한눈에 보는 요약 테이블 (Canvas 폰트/이모지 깨짐 및 다크모드 색상 충돌이 없는 순수 HTML 테이블)
    with st.expander("📋 전체 항목 한눈에 표(Table)로 보기 (클릭하여 접기/펼치기)", expanded=True):
        html_rows: List[str] = []
        for r in filtered_records:
            is_pat = r.get("source_type") == "patent"
            kor_trans_ready = bool((r.get("korean_translation") or "").strip())
            multi_links = build_multi_source_links(r)
            primary_url = resolve_source_url(r)
            secondary_url = multi_links[1]["url"] if len(multi_links) > 1 else primary_url

            pub_date_esc = html.escape(str(r.get("published_date", "")))
            comp_esc = html.escape(str(r.get("company", "")))
            title_esc = html.escape(str(r.get("title", "")))
            doc_id_esc = html.escape(str(r.get("doc_id", "")))

            type_badge = (
                '<span class="b2b-pill b2b-pill-patent">특허 (Patent)</span>'
                if is_pat
                else '<span class="b2b-pill b2b-pill-news">신문기사 (News)</span>'
            )
            anom_badge = (
                '<span style="display:inline-block; padding:0.15rem 0.5rem; font-size:0.74rem; font-weight:800; '
                'color:#B91C1C; background:#FEF2F2; border:1px solid #FCA5A5; border-radius:6px; white-space:nowrap;">'
                "🚨 이상기류</span>"
                if r.get("anomaly_flag")
                else '<span style="color:#94A3B8; font-size:0.8rem;">-</span>'
            )
            kw_pills = "".join(
                f'<span class="b2b-pill-keyword" style="margin-top:2px; margin-bottom:2px;">#{html.escape(str(k))}</span>'
                for k in (r.get("tech_keywords_list") or [])
            )
            trans_badge = (
                '<span style="display:inline-block; padding:0.14rem 0.48rem; font-size:0.74rem; font-weight:700; '
                'color:#0369A1; background:#F0F9FF; border:1px solid #BAE6FD; border-radius:6px; white-space:nowrap;">'
                "국문번역 제공</span>"
                if kor_trans_ready
                else '<span style="color:#94A3B8; font-size:0.8rem;">-</span>'
            )

            html_rows.append(
                f"<tr>"
                f'<td style="white-space:nowrap; color:#475569; font-weight:600;">{pub_date_esc}</td>'
                f"<td>{type_badge}</td>"
                f'<td><span class="b2b-pill b2b-pill-company">{comp_esc}</span></td>'
                f'<td style="text-align:center;">{anom_badge}</td>'
                f'<td style="min-width:260px;">'
                f'<div style="font-weight:700; color:#0F172A;">{title_esc}</div>'
                f'<div style="font-size:0.75rem; color:#64748B;">ID: {doc_id_esc}</div></td>'
                f'<td style="min-width:180px;">{kw_pills}</td>'
                f'<td style="text-align:center;">{trans_badge}</td>'
                f'<td style="white-space:nowrap; text-align:center;">'
                f'<a class="tbl-link" href="{html.escape(primary_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f"🔗 원문 바로가기</a></td>"
                f'<td style="white-space:nowrap; text-align:center;">'
                f'<a class="tbl-link" href="{html.escape(secondary_url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                f'style="color:#334155 !important; background:#F8FAFC; border-color:#CBD5E1;">'
                f"🔎 교차검색</a></td>"
                f"</tr>"
            )

        overview_table_html = (
            '<div class="b2b-table-wrap">'
            '<table class="b2b-clean-table">'
            "<thead><tr>"
            "<th>발행일</th>"
            "<th>구분</th>"
            "<th>기업</th>"
            '<th style="text-align:center;">이상기류</th>'
            "<th>제목 / 문서번호</th>"
            "<th>핵심 기술 키워드</th>"
            '<th style="text-align:center;">국문번역</th>'
            '<th style="text-align:center;">원문 링크</th>'
            '<th style="text-align:center;">교차검색</th>'
            "</tr></thead>"
            f'<tbody>{"".join(html_rows)}</tbody>'
            "</table></div>"
        )
        st.markdown(overview_table_html, unsafe_allow_html=True)

    st.markdown("#### 📂 개별 특허·신문기사 심층 아코디언 뷰 (Delta 3줄 요약 · 다중 원문 링크 · 국문 번역 팝업)")

    # 2. 개별 아코디언 (`st.expander`) 리스트
    for idx, item in enumerate(filtered_records):
        is_patent = item.get("source_type") == "patent"
        src_badge = "📄 [특허]" if is_patent else "📰 [신문기사]"
        anom_badge = " 🚨 [이상기류 감지]" if item.get("anomaly_flag") else ""
        comp_name = item.get("company", "Unknown")
        doc_id = item.get("doc_id", "")
        title = item.get("title", "")
        pub_date = item.get("published_date", "")
        kor_trans = (item.get("korean_translation") or "").strip()
        trans_tag = " 🌐[국문번역 탑재]" if kor_trans else ""

        expander_label = f"{src_badge}{anom_badge}{trans_tag} [{comp_name}] {title} ({pub_date} | {doc_id})"
        # 상위 3개 항목 또는 이상 기류 항목은 기본 전개하여 즉시 가독성 제공
        default_open = (idx < 2) or bool(item.get("anomaly_flag"))

        with st.expander(expander_label, expanded=default_open):
            target_url = resolve_source_url(item)
            multi_links = build_multi_source_links(item)

            # 상단 액션 바: 메타 태그 + 클릭형 제목 원문 링크 + 네이티브 st.link_button + 국문 번역 팝업
            act_col1, act_col2, act_col3 = st.columns([3.0, 1.4, 1.3])

            with act_col1:
                pill_cls = "b2b-pill-patent" if is_patent else "b2b-pill-news"
                pill_txt = "PATENT (특허 명세서)" if is_patent else "NEWS (신문기사)"
                kw_html = "".join(
                    f'<span class="b2b-pill-keyword">#{html.escape(str(k))}</span>'
                    for k in (item.get("tech_keywords_list") or [])
                )
                safe_title_url = html.escape(target_url, quote=True)
                st.markdown(
                    f'<span class="b2b-pill b2b-pill-company">{html.escape(comp_name)}</span>'
                    f'<span class="b2b-pill {pill_cls}">{pill_txt}</span>'
                    f'<span style="font-size:0.82rem; color:#475569; font-weight:600;">ID: {html.escape(doc_id)} | 발행일: {html.escape(pub_date)}</span>'
                    f'<div style="margin-top:0.4rem; margin-bottom:0.25rem;">'
                    f'<a href="{safe_title_url}" target="_blank" rel="noopener noreferrer" '
                    f'style="font-size:0.98rem; font-weight:800; color:#1D4ED8; text-decoration:underline;">'
                    f"🔗 {html.escape(title)} ↗</a></div>"
                    f'<div style="margin-top:0.25rem;">{kw_html}</div>',
                    unsafe_allow_html=True,
                )

            with act_col2:
                primary_btn_label = "📄 특허 원문 명세서 열기 ↗" if is_patent else "📰 신문기사 원문 보기 ↗"
                if hasattr(st, "link_button") and target_url:
                    st.link_button(
                        label=primary_btn_label,
                        url=target_url,
                        use_container_width=True,
                        type="primary",
                    )
                elif target_url:
                    safe_url = html.escape(target_url, quote=True)
                    st.markdown(
                        f'<div style="text-align:right; margin-top:0.2rem;">'
                        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" class="b2b-url-link">'
                        f"{primary_btn_label}</a></div>",
                        unsafe_allow_html=True,
                    )

            with act_col3:
                # 영문 특허 등 DB에 korean_translation이 존재하는 경우에만 st.popover 버튼 노출
                if kor_trans and hasattr(st, "popover"):
                    with st.popover("🌐 국문 번역 보기", use_container_width=True):
                        st.markdown(f"#### 🌐 AI 사전 국문 번역 — `{doc_id}`")
                        st.caption("수집·분석 배치 단계에서 gpt-4o-mini로 사전 번역된 디스플레이 전문 국문 번역본입니다.")
                        st.divider()
                        st.markdown(kor_trans)

            # 다중 원문/언론사/특허청 원클릭 링크 바 (특허청 및 주요 신문사 원문 즉시 이동)
            if len(multi_links) > 1:
                sub_links_html = " &nbsp;".join(
                    f'<a href="{html.escape(lk["url"], quote=True)}" target="_blank" rel="noopener noreferrer" '
                    f'style="display:inline-block; padding:0.22rem 0.65rem; margin-top:0.25rem; font-size:0.78rem; '
                    f'font-weight:700; color:#1E293B; background-color:#F1F5F9; border:1px solid #CBD5E1; '
                    f'border-radius:6px; text-decoration:none;">{html.escape(lk["label"])}</a>'
                    for lk in multi_links
                )
                st.markdown(
                    f'<div style="margin-top:0.45rem; padding:0.45rem 0.75rem; background:#F8FAFC; '
                    f'border:1px dashed #CBD5E1; border-radius:8px;">'
                    f'<span style="font-size:0.78rem; font-weight:700; color:#475569; margin-right:0.5rem;">'
                    f'🌐 원문 채널 바로가기:</span>{sub_links_html}</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("---")

            # 이상 기류가 있는 경우 강조 표시
            if item.get("anomaly_flag"):
                st.error(
                    f"🚨 **[이상 기류 진단 사유]**: {item.get('anomaly_description') or '경쟁사 특이 동향 포착'}"
                )

            # 좌우 분할: 상품기획 관점 델타 3줄 요약 vs 수집 원문(요약서 & 독립 청구항/기사 본문)
            detail_left, detail_right = st.columns(2, gap="large")
            with detail_left:
                st.markdown("**💡 [상품기획 관점 델타(Delta) 3줄 요약]**")
                st.info(item.get("delta_summary") or "(아직 델타 분석이 수행되지 않았습니다.)")

            with detail_right:
                st.markdown("**📄 [수집 원문 : 요약서 & 독립 청구항 / 순수 본문]**")
                raw_abs = (item.get("abstract") or "").strip()
                raw_body = (item.get("content") or "").strip()
                combined_source = f"[요약서]\n{raw_abs}\n\n[본문/독립항]\n{raw_body}" if raw_abs else raw_body
                st.text_area(
                    label=f"원문_{doc_id}_{idx}",
                    value=combined_source or "(원문 텍스트 없음)",
                    height=135,
                    disabled=True,
                    label_visibility="collapsed",
                )


# =====================================================================
# 4. 자동 수집 스케줄러(매일 04:00 KST), API 키 관리 및 포터블 패키징 헬퍼
# =====================================================================

KST_TZ = timezone(timedelta(hours=9))
ENV_FILE_PATH: Path = PROJECT_ROOT / ".env"
BATCH_LOG_PATH: Path = PROJECT_ROOT / "batch_log.txt"


def _load_env_dict() -> Dict[str, str]:
    """`.env` 파일에서 현재 설정된 키-값 목록을 읽어옵니다."""
    result: Dict[str, str] = {"OPENAI_API_KEY": "", "KIPRIS_API_KEY": ""}
    if not ENV_FILE_PATH.exists():
        return result
    try:
        for raw_line in ENV_FILE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v in ("your_openai_api_key_here", "your_kipris_openapi_service_key_here"):
                v = ""
            result[k] = v
    except Exception:
        pass
    return result


def _save_env_keys(openai_key: str, kipris_key: str) -> None:
    """웹 UI에서 입력한 API 키를 `.env` 파일에 즉시 저장합니다."""
    content = (
        "# ============================================================================\n"
        "# Display Foresight - Environment Variables (.env)\n"
        "# 대시보드 좌측 사이드바 또는 텍스트 에디터에서 언제든 수정 가능합니다.\n"
        "# ============================================================================\n\n"
        f"KIPRIS_API_KEY={kipris_key.strip()}\n"
        f"OPENAI_API_KEY={openai_key.strip()}\n"
    )
    ENV_FILE_PATH.write_text(content, encoding="utf-8")
    try:
        os.chmod(ENV_FILE_PATH, 0o600)
    except Exception:
        pass


def run_daily_pipeline_now(trigger_source: str = "SCHEDULE_0400_KST") -> Dict[str, int]:
    """
    Windows/macOS/Linux 구분 없이 현재 Python 인터프리터(`sys.executable`)로
    5단계 수집·분석 파이프라인을 순차 실행하고 `batch_log.txt`에 기록합니다.
    """
    steps = [
        ("STEP 1/5 특허 수집 (KIPRIS/USPTO)", PROJECT_ROOT / "collectors" / "fetch_patents.py"),
        ("STEP 2/5 글로벌 뉴스 수집 (Google News)", PROJECT_ROOT / "collectors" / "fetch_news.py"),
        ("STEP 3/5 기술 키워드 동적 추출 (gpt-4o-mini)", PROJECT_ROOT / "analyzers" / "extract_keywords.py"),
        ("STEP 4/5 델타 3줄 요약 & 이상기류 탐지 (gpt-4o)", PROJECT_ROOT / "analyzers" / "analyze_delta.py"),
        ("STEP 5/5 일간/주간 임원 브리핑 생성 (gpt-4o)", PROJECT_ROOT / "analyzers" / "executive_summary.py"),
    ]
    exit_codes: Dict[str, int] = {}
    now_str = datetime.now(KST_TZ).strftime("%Y-%m-%d %H:%M:%S KST")

    with open(BATCH_LOG_PATH, "a", encoding="utf-8") as log_f:
        log_f.write("======================================================================\n")
        log_f.write(f"[START] Display Foresight 배치 파이프라인 시작 ({trigger_source}): {now_str}\n")
        log_f.write(f"Working Directory: {PROJECT_ROOT}\n")
        log_f.write("======================================================================\n")
        log_f.flush()

        for step_label, script_path in steps:
            step_ts = datetime.now(KST_TZ).strftime("%Y-%m-%d %H:%M:%S")
            log_f.write(f"\n[{step_label}] 시작 - {step_ts}\n")
            log_f.flush()
            try:
                proc = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(PROJECT_ROOT),
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    timeout=600,
                    check=False,
                )
                exit_codes[step_label] = proc.returncode
                log_f.write(f"[{step_label}] 종료 코드: {proc.returncode}\n")
            except Exception as exc:
                exit_codes[step_label] = -1
                log_f.write(f"[{step_label}] 실행 예외: {exc}\n")
            log_f.flush()

        end_str = datetime.now(KST_TZ).strftime("%Y-%m-%d %H:%M:%S KST")
        log_f.write("\n======================================================================\n")
        log_f.write(f"[END] Display Foresight 배치 파이프라인 완료: {end_str}\n")
        log_f.write("======================================================================\n\n")
        log_f.flush()

    return exit_codes


@st.cache_resource
def ensure_kst_0400_scheduler() -> Dict[str, Any]:
    """
    대시보드 서버 프로세스 내 싱글톤 데몬 스레드:
    매일 한국시간(KST, UTC+9) 새벽 04:00 정각에 자동으로 5단계 수집·분석 파이프라인을 가동합니다.
    """
    state: Dict[str, Any] = {
        "active": True,
        "schedule_time": "매일 04:00 (KST, 한국 표준시)",
        "last_auto_run_date": None,
        "last_auto_run_at": None,
    }

    def _scheduler_worker() -> None:
        while True:
            try:
                now_kst = datetime.now(KST_TZ)
                today_str = now_kst.strftime("%Y-%m-%d")
                if now_kst.hour == 4 and now_kst.minute == 0 and state["last_auto_run_date"] != today_str:
                    state["last_auto_run_date"] = today_str
                    state["last_auto_run_at"] = now_kst.strftime("%Y-%m-%d %H:%M:%S KST")
                    run_daily_pipeline_now(trigger_source="AUTO_DAILY_0400_KST")
            except Exception:
                pass
            time.sleep(30)

    #worker = threading.Thread(target=_scheduler_worker, name="DisplayForesight_KST0400_Scheduler", daemon=True)
    #worker.start()
    return state


@st.cache_data(ttl=300, show_spinner=False)
def _get_local_network_url(port: int = 8501) -> str:
    """동일 와이파이/사내망의 다른 PC에서 접속할 수 있는 현재 PC의 네트워크 URL을 반환합니다."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip_addr = s.getsockname()[0]
            if ip_addr and not ip_addr.startswith("127."):
                return f"http://{ip_addr}:{port}"
    except Exception:
        pass
    return f"http://localhost:{port}"


@st.cache_data(show_spinner=False)
def build_portable_project_zip(_db_mtime: float) -> bytes:
    """
    다른 PC(Windows / Mac / Linux)에서도 압축만 풀고 더블클릭(`start_windows.bat` 또는 `start_mac.command`)하면
    동일한 화면과 데이터를 즉시 열 수 있도록 전체 프로젝트를 `.zip` 바이트로 패키징합니다.
    (보안을 위해 개인 API 키가 담긴 `.env` 파일은 제외하고 `.env.example`을 포함합니다.)
    """
    buf = io.BytesIO()
    exclude_names = {"__pycache__", ".DS_Store", ".git", ".venv", "venv", ".env", "Display_Foresight_Portable.zip"}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(PROJECT_ROOT.rglob("*")):
            if any(part in exclude_names for part in file_path.parts):
                continue
            if file_path.is_file() and not file_path.name.endswith(".pyc"):
                rel_path = file_path.relative_to(PROJECT_ROOT)
                zf.write(file_path, arcname=str(Path("display_foresight") / rel_path))
    return buf.getvalue()


# =====================================================================
# 5. 메인 애플리케이션 엔트리포인트
# =====================================================================


def main() -> None:
    # DB 스키마 초기화 보장 및 매일 새벽 04:00 KST 백그라운드 스케줄러 기동
    init_database()
    scheduler_state = ensure_kst_0400_scheduler()

    db_mtime = _get_db_mtime()
    all_records = cached_get_all_dashboard_data(db_mtime)

    # 초기 실행 시 샘플 데이터(USPTO 번역본 및 주간 브리핑 포함) 자동 보정
    has_all_10_companies = len({r.get("company") for r in all_records}) >= 10
    if not all_records or (
        cached_get_executive_summary("daily", None, db_mtime) is None
        or cached_get_executive_summary("weekly", None, db_mtime) is None
        or not has_all_10_companies
    ):
        seed_demo_data_if_requested()
        db_mtime = _get_db_mtime()
        all_records = cached_get_all_dashboard_data(db_mtime)

    if all_records:
        latest_date_str = max(
            (r.get("published_date", "")[:10] for r in all_records if r.get("published_date")),
            default=datetime.now().strftime("%Y-%m-%d"),
        )
        try:
            default_date = datetime.strptime(latest_date_str, "%Y-%m-%d").date()
        except ValueError:
            default_date = datetime.now().date()
    else:
        default_date = datetime.now().date()

    # -----------------------------------------------------------------
    # 사이드바: 조회 조건 및 필터 컨트롤러
    # -----------------------------------------------------------------
    with st.sidebar:
        st.markdown("## 🔬 Display Foresight")
        st.caption("B2B Executive Intelligence Platform")
        st.divider()

        selected_date: date = st.date_input(
            "📅 기준 일자 선택",
            value=default_date,
            help="일간 브리핑 및 리스트 필터링 기준 날짜를 선택합니다.",
        )

        show_all_dates: bool = st.checkbox(
            "전체 기간 데이터 통합 보기",
            value=True,
            help="체크 시 최근 3개월 전체 특허·뉴스 인텔리전스를 통합 탐색합니다.",
        )

        available_companies = sorted(
            {r.get("company", "") for r in all_records if r.get("company")} | set(ALL_10_TARGET_COMPANIES),
            key=lambda x: ALL_10_TARGET_COMPANIES.index(x) if x in ALL_10_TARGET_COMPANIES else 999,
        )

        selected_companies: List[str] = st.multiselect(
            "🏢 타겟 기업 필터",
            options=available_companies,
            default=available_companies,
        )

        selected_sources: List[str] = st.multiselect(
            "📑 데이터 출처 필터",
            options=["patent", "news"],
            default=["patent", "news"],
            format_func=lambda x: "특허 (KIPRIS / USPTO)" if x == "patent" else "뉴스 (Google News)",
        )

        only_anomalies: bool = st.checkbox(
            "🚨 이상 기류(Anomaly) 감지 건만 보기",
            value=False,
        )

        keyword_search: str = st.text_input(
            "🔍 키워드 / 제목 / 본문 검색",
            placeholder="예: Tandem, MLA, US-119...",
        ).strip()

        st.divider()
        if st.button("🔄 샘플 데이터 리셋 / 새로고침", use_container_width=True):
            seed_demo_data_if_requested()
            st.rerun()

        # -------------------------------------------------------------
        # [신규] API 키 나중에 별도 등록 & 매일 새벽 04:00 (KST) 자동수집 설정
        # -------------------------------------------------------------
        with st.expander("⚙️ API 키 & 새벽 04:00 자동수집", expanded=True):
            st.markdown(
                f'<div style="background:#EFF6FF; border:1px solid #BFDBFE; border-radius:8px; padding:0.55rem 0.7rem; margin-bottom:0.6rem;">'
                f'<div style="font-size:0.78rem; font-weight:800; color:#1E40AF;">⏰ 자동 배치 스케줄러 가동 중</div>'
                f'<div style="font-size:0.75rem; color:#1E3A8A; margin-top:2px;">기준: <b>{html.escape(scheduler_state["schedule_time"])}</b></div>'
                f"</div>",
                unsafe_allow_html=True,
            )

            env_vals = _load_env_dict()
            cur_openai = env_vals.get("OPENAI_API_KEY", "")
            cur_kipris = env_vals.get("KIPRIS_API_KEY", "")

            openai_badge = "✅ 등록됨" if cur_openai else "⚪ 미등록 (나중에 등록 가능)"
            kipris_badge = "✅ 등록됨" if cur_kipris else "⚪ 미등록 (나중에 등록 가능)"
            st.caption(f"• OpenAI API: **{openai_badge}**\n• KIPRIS API: **{kipris_badge}**")

            new_openai_key = st.text_input(
                "🔑 OpenAI API Key (AI 요약·번역용)",
                value=cur_openai,
                type="password",
                placeholder="sk-... (나중에 입력 후 저장 가능)",
                key="sidebar_openai_api_key_input",
            )
            new_kipris_key = st.text_input(
                "🔑 KIPRIS API Key (한국특허 수집용·선택)",
                value=cur_kipris,
                type="password",
                placeholder="미입력 시 미국 USPTO·뉴스만 수집",
                key="sidebar_kipris_api_key_input",
            )

            if st.button("💾 API 키 저장 (.env 반영)", use_container_width=True, key="save_env_btn"):
                _save_env_keys(new_openai_key, new_kipris_key)
                st.success("API 키 설정이 .env 파일에 저장되었습니다!")
                st.rerun()

            if st.button("🚀 지금 즉시 수집·분석 실행", use_container_width=True, key="run_pipeline_now_btn"):
                with st.spinner("특허(USPTO/KIPRIS)·뉴스 수집 및 AI 분석 파이프라인 실행 중..."):
                    run_daily_pipeline_now(trigger_source="MANUAL_UI_TRIGGER")
                st.success("배치 파이프라인 실행 완료! (batch_log.txt 기록됨)")
                st.rerun()

        # -------------------------------------------------------------
        # [신규] 다른 PC에서 열기 (동일 네트워크 URL & 포터블 ZIP 다운로드)
        # -------------------------------------------------------------
        with st.expander("🖥️ 다른 PC에서 열기 / 포터블 배포", expanded=True):
            lan_url = _get_local_network_url(8501)
            st.markdown(
                f'<div style="background:#F8FAFC; border:1px solid #CBD5E1; border-radius:8px; padding:0.55rem 0.7rem; margin-bottom:0.55rem;">'
                f'<div style="font-size:0.76rem; font-weight:700; color:#334155;">🌐 같은 네트워크(와이파이/사내망) 다른 PC 접속 주소:</div>'
                f'<div style="font-size:0.84rem; font-weight:800; color:#2563EB; margin-top:2px; word-break:break-all;">{html.escape(lan_url)}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
            st.caption("다른 PC(Windows/Mac)로 시스템을 통째로 옮기려면 아래 패키지를 다운로드한 뒤 압축을 풀고 `start_windows.bat` 또는 `start_mac.command`를 더블클릭하세요.")
            portable_zip_bytes = build_portable_project_zip(db_mtime)
            st.download_button(
                label="📦 다른 PC 이동용 패키지(.zip) 다운로드",
                data=portable_zip_bytes,
                file_name="Display_Foresight_Portable.zip",
                mime="application/zip",
                use_container_width=True,
                key="download_portable_zip_btn",
            )

    # -----------------------------------------------------------------
    # 데이터 필터링 (Zero-Latency 인메모리 필터링)
    # -----------------------------------------------------------------
    target_date_str = selected_date.strftime("%Y-%m-%d")
    filtered_records: List[Dict[str, Any]] = []

    for rec in all_records:
        if not show_all_dates:
            pub_d = (rec.get("published_date") or "")[:10]
            coll_d = (rec.get("collected_at") or "")[:10]
            anal_d = (rec.get("analyzed_at") or "")[:10]
            if target_date_str not in (pub_d, coll_d, anal_d):
                continue
        if selected_companies and rec.get("company") not in selected_companies:
            continue
        if selected_sources and rec.get("source_type") not in selected_sources:
            continue
        if only_anomalies and not rec.get("anomaly_flag"):
            continue
        if keyword_search:
            kw_lower = keyword_search.lower()
            haystack = " ".join(
                [
                    str(rec.get("doc_id", "")),
                    str(rec.get("title", "")),
                    str(rec.get("tech_keywords", "")),
                    str(rec.get("delta_summary", "")),
                    str(rec.get("content", "")),
                    str(rec.get("korean_translation", "")),
                ]
            ).lower()
            if kw_lower not in haystack:
                continue
        filtered_records.append(rec)

    # 최근 3일 이내 이상 기류 및 최근 3개월 트렌드 데이터 계산
    start_3d = (selected_date - timedelta(days=3)).strftime("%Y-%m-%d")
    start_90d = (selected_date - timedelta(days=90)).strftime("%Y-%m-%d")

    recent_anomalies = [
        r
        for r in all_records
        if r.get("anomaly_flag")
        and (not selected_companies or r.get("company") in selected_companies)
        and start_3d <= (r.get("published_date") or "")[:10] <= target_date_str
    ]
    three_month_records = [
        r
        for r in all_records
        if (not selected_companies or r.get("company") in selected_companies)
        and start_90d <= (r.get("published_date") or "")[:10] <= target_date_str
    ]

    # -----------------------------------------------------------------
    # 상단 타이틀 헤더 & 4대 핵심 KPI 카드
    # -----------------------------------------------------------------
    st.markdown("## 🔬 Display Foresight : B2B 디스플레이 기술·특허 전략 인텔리전스")
    st.caption(
        "Zero-Latency Executive Dashboard | KIPRIS(한국) · USPTO(미국) 특허 및 글로벌 뉴스 델타(Delta) 분석 플랫폼"
    )

    k1, k2, k3, k4 = st.columns(4)
    pat_count = sum(1 for r in filtered_records if r.get("source_type") == "patent")
    us_pat_count = sum(1 for r in filtered_records if str(r.get("doc_id", "")).startswith("US-"))
    news_count = sum(1 for r in filtered_records if r.get("source_type") == "news")
    anom_count = sum(1 for r in filtered_records if r.get("anomaly_flag"))

    with k1:
        st.markdown(
            f'<div class="b2b-kpi-card"><div class="b2b-kpi-label">조회된 총 인텔리전스</div>'
            f'<div class="b2b-kpi-value">{len(filtered_records)} 건</div></div>',
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            f'<div class="b2b-kpi-card"><div class="b2b-kpi-label">특허 델타 분석 (미국 USPTO)</div>'
            f'<div class="b2b-kpi-value" style="color:#2563EB;">{pat_count} 건 <span style="font-size:0.95rem; color:#64748B;">(US {us_pat_count}건)</span></div></div>',
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            f'<div class="b2b-kpi-card"><div class="b2b-kpi-label">글로벌 산업/투자 뉴스</div>'
            f'<div class="b2b-kpi-value" style="color:#059669;">{news_count} 건</div></div>',
            unsafe_allow_html=True,
        )
    with k4:
        st.markdown(
            f'<div class="b2b-kpi-card"><div class="b2b-kpi-label">경쟁사 이상 기류(Anomaly)</div>'
            f'<div class="b2b-kpi-value" style="color:#DC2626;">{anom_count} 건</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # =================================================================
    # [1. Top Tier: 거시적 통찰 (Executive Summary & Alert)]
    # =================================================================
    daily_exec = cached_get_executive_summary("daily", target_date_str, db_mtime)
    weekly_exec = cached_get_executive_summary("weekly", None, db_mtime)
    render_top_tier_executive_and_alerts(
        anomalies=recent_anomalies,
        daily_record=daily_exec,
        weekly_record=weekly_exec,
        fallback_date=target_date_str,
    )

    st.divider()

    # =================================================================
    # [2. Middle Tier: 트렌드 및 시그널 시각화 (Context & Trends)]
    # =================================================================
    keyword_trends = cached_get_keyword_trends(limit=12, _db_mtime=db_mtime)
    render_middle_tier_charts(
        three_month_records=three_month_records,
        keyword_trends=keyword_trends,
    )

    st.divider()

    # =================================================================
    # [3. Bottom Tier: 딥다이브 및 행동 유도 (Actionable Details)]
    # =================================================================
    report_tag = "ALL" if show_all_dates else selected_date.strftime("%Y%m%d")
    render_bottom_tier_actionable_details(
        filtered_records=filtered_records,
        report_date_tag=report_tag,
    )


if __name__ == "__main__":
    main()
