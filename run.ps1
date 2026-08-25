# Consultant Experience - start script (Windows PowerShell)
# Usage:  .\run.ps1          normal start
#         .\run.ps1 -Setup   create the venv and install dependencies first

param([switch]$Setup, [int]$Port = 8000)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Setup -or -not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env from the template." -ForegroundColor Yellow
    Write-Host "Add your ANTHROPIC_API_KEY to it, then run this script again." -ForegroundColor Yellow
    Write-Host "Get a key at https://console.anthropic.com/settings/keys"
    exit 1
}

Write-Host ""
Write-Host "Consultant Experience -> http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "API docs               -> http://127.0.0.1:$Port/docs"
Write-Host ""
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port $Port
