# Start-QwopusZeroTierStack.ps1
# Starts Qwopus-VL-3.6-27B on llama.cpp, then ensures LiteLLM + optional Oracle are up.

param(
    [string]$LlamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server",
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [int]$ContextSize = 65536,
    [switch]$Vision,
    [switch]$Metrics,
    [switch]$StopExisting,
    [switch]$SkipLlamaStart,
    [switch]$NoOracle,
    [int]$Webchat2ApiPort = 9000,
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [string]$BackendKey = "llama.cpp"
)

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg); Write-Host ("`n[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
function Write-Ok { param([string]$Msg); Write-Host ("  OK {0}" -f $Msg) -ForegroundColor Green }
function Write-Warn { param([string]$Msg); Write-Host ("  WARN {0}" -f $Msg) -ForegroundColor Yellow }

function Test-JsonEndpoint {
    param([string]$Uri)
    try { return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5 }
    catch { return $null }
}

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -ne $conn)
}

Write-Host ""
Write-Host "Qwopus VL ZeroTier LLM Proxy Stack" -ForegroundColor Magenta
Write-Host "==================================" -ForegroundColor Magenta

$llamaBaseUrl = "http://127.0.0.1:$LlamaPort/v1"

if (-not $SkipLlamaStart) {
    Write-Step "Phase 1: Starting Qwopus llama.cpp server"
    $llamaScript = Join-Path $LlamaRepo "scripts\start_qwopus_vl.ps1"
    if (-not (Test-Path -LiteralPath $llamaScript)) { throw "Qwopus startup script not found: $llamaScript" }

    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $llamaScript,
        "-Port", $LlamaPort,
        "-ContextSize", $ContextSize
    )
    if ($Vision) { $args += "-Vision" }
    if ($Metrics) { $args += "-Metrics" }
    if ($StopExisting) { $args += "-StopExisting" }

    & powershell.exe @args

    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    if (-not $models) { throw "Qwopus llama.cpp did not respond at $llamaBaseUrl/models" }
    Write-Ok "llama.cpp ready at $llamaBaseUrl"
} else {
    Write-Step "Phase 1: Skipping llama.cpp startup"
    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    if (-not $models) { throw "llama.cpp is not responding at $llamaBaseUrl" }
    Write-Ok "Existing llama.cpp is reachable"
}

Write-Step "Phase 2: Ensuring LiteLLM proxy"
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"
if (Test-PortListening -Port $LiteLLMPort) {
    $proxyModels = Test-JsonEndpoint -Uri "$litellmBaseUrl/models"
    if ($proxyModels) { Write-Ok "LiteLLM already running at $litellmBaseUrl" }
    else { Write-Warn "Port $LiteLLMPort is occupied but /models did not respond" }
} else {
    $proxyScript = Join-Path $PSScriptRoot "Start-Qwen36LiteLLM.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $proxyScript `
        -LlamaCppBaseUrl $llamaBaseUrl `
        -ListenHost "0.0.0.0" `
        -ListenPort $LiteLLMPort `
        -BackendKey $BackendKey
    Start-Sleep -Seconds 5
    $proxyModels = Test-JsonEndpoint -Uri "$litellmBaseUrl/models"
    if ($proxyModels) { Write-Ok "LiteLLM ready at $litellmBaseUrl" }
    else { Write-Warn "LiteLLM may still be initializing" }
}

if (-not $NoOracle) {
    Write-Step "Phase 3: Ensuring webchat2api Oracle"
    if (Test-PortListening -Port $Webchat2ApiPort) {
        Write-Ok "webchat2api already listening on port $Webchat2ApiPort"
    } else {
        $wslCmd = ("cd {0}/src; PORT={1} .venv/bin/python main.py &" -f $Webchat2ApiPath, $Webchat2ApiPort)
        Start-Process wsl.exe -ArgumentList "-e", "bash", "-lc", $wslCmd -WindowStyle Hidden
        Start-Sleep -Seconds 8
        if (Test-PortListening -Port $Webchat2ApiPort) { Write-Ok "webchat2api started on port $Webchat2ApiPort" }
        else { Write-Warn "webchat2api may still be starting" }
    }
}

$modeLabel = if ($Vision) { "vision/mmproj" } else { "text-only" }
Write-Host ""
Write-Host "Stack ready." -ForegroundColor Green
Write-Host ("  llama.cpp : {0}" -f $llamaBaseUrl)
Write-Host ("  LiteLLM   : {0}" -f $litellmBaseUrl)
Write-Host ("  Mode      : {0}" -f $modeLabel)
Write-Host ("  Context   : {0}" -f $ContextSize)
