@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"
set "PIPELINE_BAT=%~dp0run_pipeline.bat"

echo ============================================================================
echo   [Display Foresight] 매일 새벽 04:00 (KST) Windows 작업 스케줄러 등록
echo ============================================================================
echo.

schtasks /create /tn "DisplayForesight_Daily_0400" /tr "\"%PIPELINE_BAT%\"" /sc daily /st 04:00 /f

if %ERRORLEVEL% EQU 0 (
    echo [완료] 매일 새벽 04:00에 자동 수집 파이프라인(run_pipeline.bat)이 실행되도록 등록되었습니다!
) else (
    echo [안내] 관리자 권한이 필요할 수 있습니다. 마우스 우클릭 후 '관리자 권한으로 실행'을 눌러주세요.
)
echo.
pause
