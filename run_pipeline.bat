@echo off
REM ==============================================================================
REM Display Foresight - Windows Daily Batch Pipeline Runner (run_pipeline.bat)
REM
REM 설명:
REM   Windows 환경(작업 스케줄러 / CMD)에서 매일 새벽 특허(KIPRIS) 및 뉴스(Google News RSS)
REM   원문을 수집하고, gpt-4o-mini 키워드 추출 및 gpt-4o 델타/이상기류 분석을
REM   동기식(Synchronous)으로 순차 실행하는 배치 스크립트입니다.
REM   모든 실행 로그와 에러 메시지는 프로젝트 루트의 `batch_log.txt`에 누적 기록됩니다.
REM ==============================================================================

setlocal EnableExtensions EnableDelayedExpansion

REM 한글 로그 깨짐 방지를 위해 UTF-8 코드페이지(65001) 설정 및 파이썬 UTF-8 강제
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM 1. 스크립트가 위치한 프로젝트 루트 경로로 이동
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERROR] 프로젝트 디렉토리 이동 실패: %PROJECT_DIR% >&2
    exit /b 1
)

set "LOG_FILE=%PROJECT_DIR%batch_log.txt"

REM 2. 가상환경(.venv 또는 venv) 자동 탐색 및 파이썬 실행 파일 결정
set "PYTHON_CMD=python"
if exist "%PROJECT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%PROJECT_DIR%.venv\Scripts\python.exe"
) else if exist "%PROJECT_DIR%venv\Scripts\python.exe" (
    set "PYTHON_CMD=%PROJECT_DIR%venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        where py >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_CMD=py -3"
        )
    )
)

REM 3. 모든 출력 및 에러를 batch_log.txt에 누적 기록 (>> "%LOG_FILE%" 2>&1)
(
    echo ======================================================================
    echo [START] Display Foresight Windows 일일 배치 파이프라인 시작: %DATE% %TIME%
    echo Working Directory: %PROJECT_DIR%
    echo Python Executable: %PYTHON_CMD%
    echo ======================================================================

    REM 사전 점검: DB 파일이 없을 경우 초기화 보장
    if not exist "%PROJECT_DIR%display_foresight.db" (
        echo [INIT] display_foresight.db 파일이 없어 초기화를 수행합니다...
        %PYTHON_CMD% init_db.py
    )

    REM Step 1: KIPRIS 특허 수집
    echo.
    echo [STEP 1/4] KIPRIS 특허 수집 시작 ^(collectors/fetch_patents.py^) - %DATE% %TIME%
    %PYTHON_CMD% collectors\fetch_patents.py
    set "STEP1_STATUS=!ERRORLEVEL!"
    echo [STEP 1/4] 종료 코드: !STEP1_STATUS!

    REM Step 2: Google News RSS 수집 및 순수 본문 발췌
    echo.
    echo [STEP 2/4] Google News 수집 시작 ^(collectors/fetch_news.py^) - %DATE% %TIME%
    %PYTHON_CMD% collectors\fetch_news.py
    set "STEP2_STATUS=!ERRORLEVEL!"
    echo [STEP 2/4] 종료 코드: !STEP2_STATUS!

    REM Step 3: gpt-4o-mini 기반 동적 기술 키워드 추출 및 트렌드 업데이트
    echo.
    echo [STEP 3/4] 동적 기술 키워드 추출 시작 ^(analyzers/extract_keywords.py^) - %DATE% %TIME%
    %PYTHON_CMD% analyzers\extract_keywords.py
    set "STEP3_STATUS=!ERRORLEVEL!"
    echo [STEP 3/4] 종료 코드: !STEP3_STATUS!

    REM Step 4: gpt-4o 기반 델타(Delta) 3줄 요약 및 이상 기류(Anomaly) 탐지
    echo.
    echo [STEP 4/5] 심층 델타 분석 및 이상 기류 탐지 시작 ^(analyzers/analyze_delta.py^) - %DATE% %TIME%
    %PYTHON_CMD% analyzers\analyze_delta.py
    set "STEP4_STATUS=!ERRORLEVEL!"
    echo [STEP 4/5] 종료 코드: !STEP4_STATUS!

    REM Step 5: 일간(Daily) 및 주간(Weekly - 월요일 자동 감지) 종합 요약 생성
    echo.
    echo [STEP 5/5] 일간/주간 Executive Summary 생성 시작 ^(analyzers/executive_summary.py^) - %DATE% %TIME%
    %PYTHON_CMD% analyzers\executive_summary.py
    set "STEP5_STATUS=!ERRORLEVEL!"
    echo [STEP 5/5] 종료 코드: !STEP5_STATUS!

    echo.
    echo ======================================================================
    echo [END] Display Foresight Windows 일일 배치 파이프라인 완료: %DATE% %TIME%
    echo Summary Exit Codes -^> Step1:!STEP1_STATUS!, Step2:!STEP2_STATUS!, Step3:!STEP3_STATUS!, Step4:!STEP4_STATUS!, Step5:!STEP5_STATUS!
    echo ======================================================================
    echo.
) >> "%LOG_FILE%" 2>&1

endlocal
exit /b 0
