[CmdletBinding()]
param(
    [int]$Port = 8787,
    [string]$LiteLLMUpstream = "http://127.0.0.1:4000/v1",
    [int]$ProtectRecent = 12,
    [int]$MinTokens = 1000,
    [switch]$ForceKompress
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot ".runtime\headroom"
$headroomExe = Join-Path $runtimeRoot ".venv\Scripts\headroom.exe"
$logFile = Join-Path $runtimeRoot "headroom.log"
$workerFile = Join-Path $runtimeRoot "headroom.worker.ps1"

if (-not (Test-Path -LiteralPath $headroomExe)) {
    throw "Headroom is not installed. Run .\scripts\windows\Install-HeadroomHermes.ps1 first."
}

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Host "Headroom already listens on port $Port" -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$worker = @'
Set-Location "__REPO_ROOT__"
$env:HEADROOM_TELEMETRY = "off"
$env:OPENAI_TARGET_API_URL = "__UPSTREAM__"
$env:HEADROOM_EXCLUDE_TOOLS = "read_file,headroom_retrieve"
$env:HEADROOM_MIN_TOKENS = "__MIN_TOKENS__"
$env:HEADROOM_PROTECT_RECENT = "__PROTECT_RECENT__"
$env:HEADROOM_FORCE_KOMPRESS = "__FORCE_KOMPRESS__"
& "__HEADROOM_EXE__" proxy --host 0.0.0.0 --port __PORT__ --mode token --intercept-tool-results --no-subscription-tracking --no-telemetry --memory *> "__LOG_FILE__"
'@
$worker = $worker.Replace("__UPSTREAM__", $LiteLLMUpstream).
    Replace("__REPO_ROOT__", $repoRoot).
    Replace("__MIN_TOKENS__", $MinTokens).
    Replace("__PROTECT_RECENT__", $ProtectRecent).
    Replace("__FORCE_KOMPRESS__", $(if ($ForceKompress) { "1" } else { "0" })).
    Replace("__HEADROOM_EXE__", $headroomExe).
    Replace("__PORT__", $Port).
    Replace("__LOG_FILE__", $logFile)
Set-Content -LiteralPath $workerFile -Value $worker -Encoding ASCII
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $workerFile
) | Out-Null

for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        Write-Host "Headroom ready: http://127.0.0.1:$Port/v1 -> $LiteLLMUpstream" -ForegroundColor Green
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    }
}

throw "Headroom did not become healthy. See $logFile"
