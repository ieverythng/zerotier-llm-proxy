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
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health) {
            Write-Host "Headroom already listens on port $Port" -ForegroundColor Yellow
            exit 0
        }
    } catch {
        Write-Warning "Headroom has an unhealthy listener on port $Port; attempting to replace it."
    }

    $owners = @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            Where-Object { $_ -and $_ -ne 0 }
    )
    foreach ($owner in $owners) {
        try { & taskkill.exe /PID ([int]$owner) /T /F *> $null } catch { }
        Stop-Process -Id ([int]$owner) -Force -ErrorAction SilentlyContinue
    }
    for ($wait = 0; $wait -lt 20; $wait++) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Headroom port $Port is occupied by an unhealthy listener that could not be stopped."
    }
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$worker = @'
Set-Location "__REPO_ROOT__"
$env:HEADROOM_TELEMETRY = "off"
$env:HEADROOM_ROLLOUT_CHANNEL = "canary"
# v0.37 shares sticky tool definitions across Chat and Responses even though
# their function schemas differ. Per-request injection preserves both formats
# and keeps memory enabled without replaying the other API's schema.
$env:HEADROOM_TOOL_INJECTION_STICKY = "disabled"
$env:OPENAI_TARGET_API_URL = "__UPSTREAM__"
$env:HEADROOM_EXCLUDE_TOOLS = "read_file,headroom_retrieve"
$env:HEADROOM_MIN_TOKENS = "__MIN_TOKENS__"
$env:HEADROOM_PROTECT_RECENT = "__PROTECT_RECENT__"
$env:HEADROOM_FORCE_KOMPRESS = "__FORCE_KOMPRESS__"
# Hermes/Discord and local Codex share one intentional user-scoped memory store.
# Explicit global mode also keeps Responses memory-save and memory-search on the
# same backend while Headroom's per-project continuation seam is repaired upstream.
& "__HEADROOM_EXE__" proxy --host 0.0.0.0 --port __PORT__ --mode token --intercept-tool-results --no-subscription-tracking --no-telemetry --memory --memory-storage global *> "__LOG_FILE__"
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
