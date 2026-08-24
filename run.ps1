# run.ps1 - launch the IEEE SPS Committee Copilot (FastAPI backend + Streamlit UI)
# Usage:  ./run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "No .env found - copying .env.example. Edit it to set your chapter details." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

Write-Host "Starting backend  -> http://127.0.0.1:8000/docs" -ForegroundColor Cyan
Start-Process -FilePath "python" `
  -ArgumentList "-m","uvicorn","backend.main:app","--port","8000" `
  -WorkingDirectory $PSScriptRoot

Start-Sleep -Seconds 4
Write-Host "Starting UI       -> http://localhost:8501" -ForegroundColor Cyan
python -m streamlit run frontend/app.py
