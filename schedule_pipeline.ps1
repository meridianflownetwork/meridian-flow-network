# Register or run the Meridian Flow Network pipeline on this Windows machine.
#   .\schedule_pipeline.ps1              # run one cycle now
#   .\schedule_pipeline.ps1 -Register    # daily 06:00 Task Scheduler job
#   .\schedule_pipeline.ps1 -Unregister

param(
    [switch]$Register,
    [switch]$Unregister,
    [string]$Time = "06:00",
    [switch]$Mock
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Runner = Join-Path $Root "run_scheduled_pipeline.py"
$TaskName = "MeridianFlowNetworkPipeline"

if (-not (Test-Path $Python)) {
    throw "Virtualenv missing. Run .\.venv\Scripts\python.exe -m pip install -r requirements.txt first."
}

if ($Unregister) {
    schtasks /Delete /TN $TaskName /F
    Write-Host "Removed scheduled task $TaskName"
    exit 0
}

$Flags = @()
if ($Mock) { $Flags += "--mock" }

if ($Register) {
    $Argument = "`"$Runner`" $($Flags -join ' ')"
    $Action = "cmd.exe /c cd /d `"$Root`" && `"$Python`" $Argument >> `"$Root\logs\scheduler.log`" 2>&1"
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
    schtasks /Create /F /TN $TaskName /SC DAILY /ST $Time /TR $Action /RL LIMITED
    Write-Host "Registered $TaskName daily at $Time"
    Write-Host "Run now with:  .\.venv\Scripts\python.exe run_scheduled_pipeline.py --mock"
    exit 0
}

& $Python $Runner @Flags
exit $LASTEXITCODE
