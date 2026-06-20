# Start-Qwen36ZeroTierStack.ps1
# =============================================
# Unified startup script for the ZeroTier LLM Proxy stack.
# Launches: llama.cpp → LiteLLM proxy. webchat2api is opt-in.
#
# Usage:
#   .\Start-Qwen36ZeroTierStack.ps1
#   .\Start-Qwen36ZeroTierStack.ps1 -Model qwopus-3.6-27b -SkipLlamaStart
#   .\Start-Qwen36ZeroTierStack.ps1 -EnableOracle
# =============================================

param(
    [string]$LlamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server",
    [string]$LlamaScript = "scripts\start_turbo_hermes.ps1",
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [string]$Model = "qwen36-turbo-hermes",
    [string]$Profile = "hermes-qwen36-64k",
    [int]$ContextSize = 65536,
    [string]$ModelPath = "",
    [string]$BackendKey = "llama.cpp",
    [switch]$Metrics,
    [switch]$SkipLlamaStart,
    [switch]$ReplaceLiteLLM,
    [switch]$EnableHeadroom,
    [switch]$RouteHermesThroughHeadroom,
    [int]$HeadroomPort = 8787,
    [switch]$ForceHeadroomCompression,
    [switch]$EnableOracle,
    [switch]$NoOracle,
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [int]$Webchat2ApiPort = 9000
)

$ErrorActionPreference = "Stop"
$oracleEnabled = $EnableOracle -and -not $NoOracle

# ─── Color helpers ──────────────────────────────────────────────
function Write-Step { param([string]$Msg); Write-Host ("`n[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg); Write-Host ("  [OK] {0}" -f $Msg) -ForegroundColor Green }
function Write-Warn { param([string]$Msg); Write-Host ("  [WARN] {0}" -f $Msg) -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg); Write-Host ("  [FAIL] {0}" -f $Msg) -ForegroundColor Red }

# ─── Health check helper ────────────────────────────────────────
function Test-JsonEndpoint {
    param([string]$Uri)
    try { return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5 }
    catch { return $null }
}

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return ($conn -ne $null)
}

# ─── Phase 0: Banner ────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "       ZeroTier LLM Proxy Stack - Startup Script" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host ""

# ─── Phase 1: llama.cpp ─────────────────────────────────────────
$llamaBaseUrl = "http://127.0.0.1:$LlamaPort/v1"

if (-not $SkipLlamaStart) {
    Write-Step "Phase 1: Starting llama.cpp server"

    $resolvedLlamaRepo = Resolve-Path -LiteralPath $LlamaRepo
    $resolvedLlamaScript = Join-Path $resolvedLlamaRepo $LlamaScript

    if (-not (Test-Path -LiteralPath $resolvedLlamaScript)) {
        Write-Fail "llama.cpp startup script not found: $resolvedLlamaScript"
        throw "Cannot proceed without llama.cpp startup script."
    }

    $existingModels = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    $existingNames = @()
    if ($existingModels.data) { $existingNames += @($existingModels.data | ForEach-Object { $_.id }) }
    if ($existingModels.models) { $existingNames += @($existingModels.models | ForEach-Object { $_.id; $_.name; $_.model }) }
    if ($existingModels -and $existingNames -contains $Model) {
        Write-Ok "llama.cpp already healthy at $llamaBaseUrl (model: $Model)"
        $SkipLlamaStart = $true
    }

    if (-not $SkipLlamaStart) {
    $llamaArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $resolvedLlamaScript,
        "-Port", $LlamaPort,
        "-Profile", $Profile,
        "-ContextSize", $ContextSize
    )

    if ($ModelPath) { $llamaArgs += @("-ModelPath", $ModelPath) }
    if ($Metrics)   { $llamaArgs += " -Metrics" }

    Write-Host "  Running: powershell $($llamaArgs -join ' ')" -ForegroundColor DarkGray
    & powershell.exe @llamaArgs

    # Wait for llama.cpp to be ready
    Write-Host "  Waiting for llama.cpp to initialize..." -ForegroundColor DarkGray
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
        $modelNames = @()
        if ($models.data) { $modelNames += @($models.data | ForEach-Object { $_.id }) }
        if ($models.models) { $modelNames += @($models.models | ForEach-Object { $_.id; $_.name; $_.model }) }
        if ($models -and $modelNames -contains $Model) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        Write-Fail "llama.cpp did not become ready within 60s"
        throw "llama.cpp failed to start or load model '$Model'."
    }

    Write-Ok "llama.cpp ready at $llamaBaseUrl (model: $Model)"
    }
} else {
    Write-Step "Phase 1: Skipping llama.cpp startup (user provided)"

    # Verify it's already running
    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    if (-not $models) {
        throw "llama.cpp is not responding at $llamaBaseUrl. Start it first or remove -SkipLlamaStart."
    }

    $modelNames = @()
    if ($models.data) { $modelNames += @($models.data | ForEach-Object { $_.id }) }
    if ($models.models) { $modelNames += @($models.models | ForEach-Object { $_.id; $_.name; $_.model }) }
    $hasModel = $modelNames | Where-Object { $_ -eq $Model }
    if (-not $hasModel) {
        Write-Warn "Model '$Model' not found in llama.cpp. Available: $($models.data.id -join ', ')"
    } else {
        Write-Ok "llama.cpp confirmed at $llamaBaseUrl"
    }
}

# ─── Phase 2: LiteLLM Proxy ─────────────────────────────────────
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"

if ($RouteHermesThroughHeadroom -and -not $EnableHeadroom) {
    throw "-RouteHermesThroughHeadroom requires -EnableHeadroom."
}

Write-Step "Phase 2: Starting LiteLLM proxy"

if (Test-PortListening -Port $LiteLLMPort) {
    if ($ReplaceLiteLLM) {
        Get-NetTCPConnection -State Listen -LocalPort $LiteLLMPort -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    $proxyModels = if (-not $ReplaceLiteLLM) { Test-JsonEndpoint -Uri "$litellmBaseUrl/models" } else { $null }
    if ($proxyModels) {
        Write-Ok "LiteLLM already running at $litellmBaseUrl"
    } else {
        Write-Warn "Port $LiteLLMPort occupied but not serving LiteLLM - you may need to kill the process."
    }
}
if (-not (Test-PortListening -Port $LiteLLMPort)) {
    $proxyScript = Join-Path $PSScriptRoot "Start-Qwen36LiteLLM.ps1"

    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "LiteLLM startup script not found: $proxyScript"
    }

    Write-Host "  Running LiteLLM on port $LiteLLMPort..." -ForegroundColor DarkGray
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $proxyScript,
        "-LlamaCppBaseUrl", $llamaBaseUrl,
        "-ListenHost", "0.0.0.0",
        "-ListenPort", $LiteLLMPort,
        "-BackendKey", "local-qwen36"
    ) | Out-Null

    # Wait for LiteLLM
    Start-Sleep -Seconds 5
    $proxyReady = Test-JsonEndpoint -Uri "$litellmBaseUrl/models"
    if ($proxyReady) {
        Write-Ok "LiteLLM ready at $litellmBaseUrl"
    } else {
        Write-Warn "LiteLLM may still be initializing..."
    }
}

# ─── Phase 3: webchat2api (Oracle) ──────────────────────────────

if ($EnableHeadroom) {
    Write-Step "Phase 2b: Starting Headroom context proxy"
    $headroomScript = Join-Path $PSScriptRoot "Start-HeadroomHermes.ps1"
    if ($ForceHeadroomCompression) {
        & $headroomScript -Port $HeadroomPort -ProtectRecent 0 -ForceKompress
    } else {
        & $headroomScript -Port $HeadroomPort
    }

    if ($RouteHermesThroughHeadroom) {
        $routeScript = Join-Path $PSScriptRoot "Set-HermesHeadroomRoute.ps1"
        & $routeScript -Enable -Port $HeadroomPort
        Write-Ok "Hermes now routes through Headroom on port $HeadroomPort"
    } else {
        Write-Ok "Headroom is running but Hermes routing remains unchanged"
    }
}

if ($oracleEnabled) {
    Write-Step "Phase 3: Starting webchat2api (GPT-5 Oracle)"

    if (Test-PortListening -Port $Webchat2ApiPort) {
        # Check if it's actually webchat2api
        $health = Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"
        if ($health) {
            Write-Ok "webchat2api already running on port $Webchat2ApiPort"
        } else {
            Write-Warn "Port $Webchat2ApiPort occupied by unknown service"
        }
    } else {
        Write-Host "  Launching webchat2api via WSL..." -ForegroundColor DarkGray

        $wslCmd = "cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py &"

        # Run in background via WSL
        Start-Process wsl.exe -ArgumentList "-e", "-c", $wslCmd -WindowStyle Hidden

        # Wait for webchat2api to initialize
        Start-Sleep -Seconds 8
        $oracleHealth = Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"
        if ($oracleHealth) {
            Write-Ok "webchat2api ready on port $Webchat2ApiPort"
        } else {
            Write-Warn "webchat2api may still be starting - check WSL logs if Oracle calls fail"
            $oracleLogHint = "tail -f /home/juanbeck/webchat2api/src/data/logs/*.log"
            Write-Host ("  Tip: Run wsl.exe -d Ubuntu -- bash -lc {0} to monitor" -f $oracleLogHint) -ForegroundColor DarkGray
        }
    }
} else {
    Write-Step "Phase 3: Skipping webchat2api (use -EnableOracle to start it)"
}

# ─── Summary ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "====================================================" -ForegroundColor Magenta
Write-Host "  Stack Status Summary:" -ForegroundColor Green
Write-Host ""

$llamaUp = $null -ne (Test-JsonEndpoint -Uri "$llamaBaseUrl/models")
$litellmUp = $null -ne (Test-JsonEndpoint -Uri "$litellmBaseUrl/models")
$headroomUp = $EnableHeadroom -and ($null -ne (Test-JsonEndpoint -Uri "http://127.0.0.1:$HeadroomPort/readyz"))
$oracleUp = $oracleEnabled -and ($null -ne (Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"))

Write-Host "  llama.cpp    : $(if($llamaUp){'[OK] Running'}else{'[FAIL] Not running'}) on port $LlamaPort" `
    -ForegroundColor $(if($llamaUp){'Green'}else{'Red'})
Write-Host "  LiteLLM      : $(if($litellmUp){'[OK] Running'}else{'[FAIL] Not running'}) on port $LiteLLMPort" `
    -ForegroundColor $(if($litellmUp){'Green'}else{'Red'})
if ($EnableHeadroom) {
    Write-Host "  Headroom     : $(if($headroomUp){'[OK] Running (memory enabled)'}else{'[FAIL] Not running'}) on port $HeadroomPort" `
        -ForegroundColor $(if($headroomUp){'Green'}else{'Red'})
}
Write-Host "  webchat2api  : $(if($oracleUp){'[OK] Running'}else{'[SKIP] Skipped'}) on port $Webchat2ApiPort" `
    -ForegroundColor $(if($oracleUp){'Green'}else{'Yellow'})

Write-Host ""
Write-Host "  ZeroTier Network: 3b19b3a716937e29" -ForegroundColor DarkGray
$ztIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "10.88.*" })
if ($ztIp) {
    Write-Host "  ZeroTier IP     : $($ztIp.IPAddress)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Endpoints:" -ForegroundColor DarkGray
Write-Host "    Local:  http://127.0.0.1:$LlamaPort/v1   (llama.cpp direct)" -ForegroundColor DarkGray
Write-Host "    Proxy:  http://127.0.0.1:$LiteLLMPort/v1   (LiteLLM OpenAI-compatible)" -ForegroundColor DarkGray
if ($EnableHeadroom) {
    Write-Host "    Headroom: http://127.0.0.1:$HeadroomPort/v1  (context optimization + memory)" -ForegroundColor DarkGray
}
if ($oracleEnabled) {
    Write-Host "    Oracle: http://127.0.0.1:$Webchat2ApiPort/v1  (webchat2api GPT-5)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor Magenta
Write-Host ""
if ($host.Name -eq "ConsoleHost" -and -not [Console]::IsInputRedirected) {
    Write-Host "Press any key to exit this summary (services keep running in background)..."
    $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
