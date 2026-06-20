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
$hostFile = Join-Path $runtimeRoot "headroom.host.cmd"

if (-not (Test-Path -LiteralPath $headroomExe)) {
    throw "Headroom is not installed. Run .\scripts\windows\Install-HeadroomHermes.ps1 first."
}

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Host "Headroom already listens on port $Port" -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$command = @"
@echo off
set HEADROOM_TELEMETRY=off
set OPENAI_TARGET_API_URL=$LiteLLMUpstream
set HEADROOM_EXCLUDE_TOOLS=read_file,headroom_retrieve
set HEADROOM_MIN_TOKENS=$MinTokens
set HEADROOM_PROTECT_RECENT=$ProtectRecent
set HEADROOM_FORCE_KOMPRESS=$(if ($ForceKompress) { "1" } else { "0" })
"$headroomExe" proxy --host 0.0.0.0 --port $Port --mode token --intercept-tool-results --no-subscription-tracking --no-telemetry > "$logFile" 2>&1
"@
Set-Content -LiteralPath $hostFile -Value $command -Encoding ASCII
Start-Process cmd.exe -ArgumentList @("/d", "/c", $hostFile) -WindowStyle Hidden | Out-Null

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
