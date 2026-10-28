param([switch]$Phone)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot 'scripts\start_local_ai.ps1')
$env:OLLAMA_MODEL = 'qwen3:4b-instruct'
Write-Output 'Loading the offline model. First startup may take about 30 seconds...'
$null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -ContentType 'application/json' -Body '{"model":"qwen3:4b-instruct","prompt":"","stream":false,"keep_alive":"30m","options":{"num_ctx":4096}}' -TimeoutSec 120
if ($Phone) {
    Write-Output 'Phone demo: connect laptop and phone to the same trusted Wi-Fi or hotspot.'
    Write-Output 'Open the Network URL shown below on the phone. Keep this terminal open.'
    & '.\.venv\Scripts\python.exe' -m streamlit run app.py --server.address 0.0.0.0 --server.headless true
} else {
    & '.\.venv\Scripts\python.exe' -m streamlit run app.py --server.address 127.0.0.1
}
