param()
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectDirectory '.local-ai'
$archiveFile = Join-Path $runtimeDirectory 'ollama-windows-amd64.zip'
$installDirectory = Join-Path $runtimeDirectory 'ollama'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $installDirectory 'ollama.exe'))) {
    Write-Output 'Downloading official Ollama v0.40.11 (about 1.6 GB) to D:...'
    & curl.exe --fail --location --retry 2 --continue-at - --output $archiveFile 'https://github.com/ollama/ollama/releases/download/v0.40.11/ollama-windows-amd64.zip'
    if ($LASTEXITCODE -ne 0) { throw 'Download did not complete. Run this script again to resume.' }
    $expectedChecksum = 'bda4a493ba8a7c24d57263cd843cd68cee84f44aed4299eeed532acad7eda12a'
    if ((Get-FileHash -LiteralPath $archiveFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedChecksum) {
        throw 'Download checksum does not match the official release. Software was not run.'
    }
    Write-Output 'Checksum verified. Extracting Ollama...'
    Expand-Archive -LiteralPath $archiveFile -DestinationPath $installDirectory -Force
}
& (Join-Path $PSScriptRoot 'start_local_ai.ps1')
Write-Output 'Downloading Qwen3 4B (about 2.5 GB). This is a one-time download...'
$pullResponse = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11434/api/pull' -ContentType 'application/json' -Body '{"model":"qwen3:4b-instruct","stream":false}' -TimeoutSec 7200
if ($pullResponse.status -ne 'success') { throw 'The model download did not finish successfully.' }
Write-Output 'Offline AI installed. Model: qwen3:4b-instruct. Software and models are on D:.'
