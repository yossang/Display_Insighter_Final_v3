# 🔬 Display Foresight
**B2B 디스플레이 기술·특허 전략 인텔리전스 플랫폼 (3-Tier Progressive Disclosure Dashboard)**

한·미(KIPRIS · USPTO) 특허 및 글로벌 뉴스 데이터를 일일 배치로 수집·분석하고, **Zero-Latency(`@st.cache_data`)** 기반의 B2B 화이트/슬레이트 그레이 라이트 테마 대시보드로 시각화하는 전략 인텔리전스 시스템입니다.

---

## 📂 프로젝트 구조

```text
display_foresight/
├── main.py                      # Streamlit B2B 3-Tier 프론트엔드 대시보드 (Zero-Latency)
├── init_db.py                   # SQLite DB 스키마 초기화 스크립트
├── display_foresight.db         # 사전 분석 완료된 데모/운용 SQLite 데이터베이스
├── requirements.txt             # 필수 패키지 목록
├── .env.example                 # API 키 설정 템플릿
├── .streamlit/
│   └── config.toml              # Streamlit B2B 라이트 테마 설정
├── collectors/
│   ├── fetch_patents.py         # 한국(KIPRIS) 및 미국(USPTO PatentsView) 특허 수집기
│   └── fetch_news.py            # Google News RSS 기반 글로벌 디스플레이 뉴스 수집기
├── analyzers/
│   ├── extract_keywords.py      # AI 동적 기술 키워드 추출 및 트렌드 집계
│   ├── analyze_delta.py         # 상품기획 관점 3줄 델타 요약, 이상 기류 탐지 및 영문 특허 국문 번역
│   └── executive_summary.py     # 일간(Daily) 및 주간(Weekly CSO) 종합 브리핑 생성
├── utils/
│   └── db_helper.py             # SQLite CRUD 및 대시보드 조회 헬퍼
└── run_pipeline.sh / .bat / .ps1 # 5단계 일일 자동화 배치 스크립트 (Mac/Linux/Windows)
```

---

## 🚀 빠른 실행 방법

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 대시보드 실행 (Zero-Latency)
```bash
streamlit run main.py
```

### 3. 일일 배치 파이프라인 실행 (선택)
프로젝트 루트에 `.env` 파일을 생성하고 API 키(`OPENAI_API_KEY`, `KIPRIS_API_KEY`)를 설정한 뒤 실행합니다.
```bash
# macOS / Linux
./run_pipeline.sh

# Windows (CMD / PowerShell)
run_pipeline.bat
```
