param([string[]]$AllowedEmail)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($AllowedEmail) {
    throw 'Cloudflare email-PIN Quick Tunnels currently expire before Streamlit widgets load. Run .\Start-Remote-Demo.ps1 without -AllowedEmail; LearnWave now uses a private app passcode.'
}
$runtimeDirectory = Join-Path $PSScriptRoot '.local-ai'
$tunnelExecutable = Join-Path $runtimeDirectory 'cloudflared.exe'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $tunnelExecutable)) {
    $releaseInfo = Invoke-RestMethod -Uri 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest'
    $asset = $releaseInfo.assets | Where-Object { $_.name -eq 'cloudflared-windows-amd64.exe' } | Select-Object -First 1
    if (-not $asset -or $asset.digest -notmatch '^sha256:[a-fA-F0-9]{64}$') { throw 'Could not verify the official cloudflared download.' }
    $temporaryDownload = Join-Path $runtimeDirectory 'cloudflared.download'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $temporaryDownload
    if ((Get-FileHash -LiteralPath $temporaryDownload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $asset.digest.Substring(7).ToLowerInvariant()) { throw 'Cloudflared checksum mismatch. No software was run.' }
    Move-Item -LiteralPath $temporaryDownload -Destination $tunnelExecutable
}
$passcodePath = Join-Path $runtimeDirectory 'remote-demo-passcode.txt'
if (-not (Test-Path -LiteralPath $passcodePath)) {
    $randomBytes = [byte[]]::new(18)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    $passcode = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    [System.IO.File]::WriteAllText($passcodePath, $passcode, [System.Text.Encoding]::ASCII)
} else {
    $passcode = [System.IO.File]::ReadAllText($passcodePath, [System.Text.Encoding]::ASCII).Trim()
}
if ($passcode.Length -lt 20) { throw 'The private passcode file is invalid. Remove it locally and start the script again to create a new one.' }
& (Join-Path $PSScriptRoot 'scripts\start_local_ai.ps1')
$env:OLLAMA_MODEL = 'qwen3:4b-instruct'
Write-Output 'Loading the offline model before opening the demo...'
$null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -ContentType 'application/json' -Body '{"model":"qwen3:4b-instruct","prompt":"","stream":false,"keep_alive":"30m","options":{"num_ctx":4096}}' -TimeoutSec 120
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'The project virtual environment is missing. Run the setup instructions in README.md first.' }
$streamlitVersion = & $pythonExecutable -c "import streamlit; print(streamlit.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Streamlit version from the project virtual environment.' }
if ($streamlitVersion.Trim() -ne '1.66.1') {
    throw "This demo is pinned to Streamlit 1.66.1 so the browser page and its hashed JavaScript files stay in sync. Run: .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}
Write-Output "Using Streamlit $($streamlitVersion.Trim()) (pinned frontend bundle)."
# Use a dedicated port so the local development app stays open.
$remotePort = 8503
$portOccupied = $false
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$remotePort/_stcore/health" -TimeoutSec 2
    $portOccupied = $true
} catch { }
if ($portOccupied) { throw "Port $remotePort already has an app. Stop the earlier remote demo before starting another." }
$appProcess = $null
$env:LEARNWAVE_REMOTE_DEMO = '1'
$env:LEARNWAVE_REQUIRE_LOGIN = 'false'
$env:LEARNWAVE_DEMO_PASSWORD = $passcode
try {
    $appProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','streamlit','run','app.py','--server.address','127.0.0.1','--server.port',"$remotePort",'--server.headless','true') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'remote-app.stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'remote-app.stderr.log')
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($appProcess.HasExited) { throw "The demo server could not start. Port $remotePort may be in use." }
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:$remotePort/_stcore/health" -TimeoutSec 2
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'The demo app did not become ready. No public tunnel was created.' }
    $tunnelArguments = @('tunnel','--url',"http://127.0.0.1:$remotePort")
    Write-Output 'Share the HTTPS URL shown below and this LearnWave-only passcode with your partner:'
    Write-Output $passcode
    Write-Output 'Anyone with both the link and passcode can enter. Keep this terminal and laptop on.'
    Write-Output 'Press Ctrl+C to end access. This temporary demo is not permanent hosting.'
    & $tunnelExecutable @tunnelArguments
} finally {
    if ($appProcess -and -not $appProcess.HasExited) { Stop-Process -Id $appProcess.Id }
    Remove-Item Env:\LEARNWAVE_REMOTE_DEMO -ErrorAction SilentlyContinue
    Remove-Item Env:\LEARNWAVE_REQUIRE_LOGIN -ErrorAction SilentlyContinue
    Remove-Item Env:\LEARNWAVE_DEMO_PASSWORD -ErrorAction SilentlyContinue
}
