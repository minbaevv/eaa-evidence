$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -3.13 -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m playwright install chromium
& ".\.venv\Scripts\python.exe" scripts\fetch_axe.py

Write-Host "Setup complete. Run .\run.ps1"
