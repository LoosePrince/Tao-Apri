$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[start] Creating virtual environment..."
    python -m venv .venv
}

Write-Host "[start] Installing dependencies..."
# & ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"

Write-Host "[start] Starting API server..."
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload
