# ==============================================================================
# Display Foresight - Windows PowerShell Pipeline Runner (run_pipeline.ps1)
# ==============================================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $ProjectDir

$LogFile = Join-Path $ProjectDir "batch_log.txt"

# 가상환경 자동 탐색
$PythonExe = "python"
if (Test-Path (Join-Path $ProjectDir ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
} elseif (Test-Path (Join-Path $ProjectDir "venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectDir "venv\Scripts\python.exe"
}

function Write-BatchLog([string]$Message) {
    $Message | Out-File -FilePath $LogFile -Append -Encoding utf8
}

Write-BatchLog "======================================================================"
Write-BatchLog "[START] Display Foresight PowerShell 일일 배치 파이프라인 시작: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-BatchLog "Working Directory: $ProjectDir"
Write-BatchLog "======================================================================"

if (-not (Test-Path (Join-Path $ProjectDir "display_foresight.db"))) {
    Write-BatchLog "[INIT] display_foresight.db 파일이 없어 초기화를 수행합니다..."
    & $PythonExe init_db.py *>> $LogFile
}

$Steps = @(
    @{ Name = "[STEP 1/4] KIPRIS 특허 수집"; Script = "collectors/fetch_patents.py" },
    @{ Name = "[STEP 2/4] Google News 수집"; Script = "collectors/fetch_news.py" },
    @{ Name = "[STEP 3/4] 동적 기술 키워드 추출"; Script = "analyzers/extract_keywords.py" },
    @{ Name = "[STEP 4/4] 심층 델타 분석 및 이상 기류 탐지"; Script = "analyzers/analyze_delta.py" }
)

foreach ($Step in $Steps) {
    Write-BatchLog ""
    Write-BatchLog "$($Step.Name) 시작 ($($Step.Script)) - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    & $PythonExe $Step.Script *>> $LogFile
    Write-BatchLog "$($Step.Name) 종료 코드: $LASTEXITCODE"
}

Write-BatchLog ""
Write-BatchLog "======================================================================"
Write-BatchLog "[END] Display Foresight PowerShell 일일 배치 파이프라인 완료: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-BatchLog "======================================================================"
