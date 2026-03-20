$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "outputs"
$serverLog = Join-Path $logDir "server.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

& $python -m uvicorn main:app --host 127.0.0.1 --port 8000 *>> $serverLog
