param()
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectDirectory '.local-ai'
$ollamaExecutable = Join-Path $runtimeDirectory 'ollama\ollama.exe'
if (-not (Test-Path -LiteralPath $ollamaExecutable)) {
    throw 'Local AI is not installed yet. Run scripts/setup_local_ai.ps1 once with internet access.'
}
$env:OLLAMA_MODELS = Join-Path $runtimeDirectory 'models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
    Write-Output 'Local AI server is already running.'
    return
} catch { }
New-Item -ItemType Directory -Path $env:OLLAMA_MODELS -Force | Out-Null
Start-Process -FilePath $ollamaExecutable -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $runtimeDirectory -RedirectStandardOutput (Join-Path $runtimeDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'server.stderr.log') | Out-Null
for ($check = 0; $check -lt 30; $check++) {
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2
        Write-Output 'Local AI server is ready (private to this laptop).'
        return
    } catch { Start-Sleep -Seconds 1 }
}
throw 'Local AI did not start. See .local-ai/server.stderr.log.'
