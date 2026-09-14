# Launch setup_schema.py with a real interpreter (never the Windows Store stub).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$LocalPython = Join-Path $env:LOCALAPPDATA "Python\bin\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} elseif (Test-Path $LocalPython) {
    $Python = $LocalPython
} else {
    Write-Host "No project venv or local Python found. setup_schema.py will try to discover one." -ForegroundColor Cyan
    $Python = "python"
}

& $Python (Join-Path $Root "setup_schema.py") @args
exit $LASTEXITCODE
