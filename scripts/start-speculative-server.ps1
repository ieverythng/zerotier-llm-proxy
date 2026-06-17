<#
.SYNOPSIS
    Start llama.cpp server with speculative decoding (DFlash) via BeeLlama build.
    Supports two modes: BeeLlama draft (Qwen3.6 DFlash) and Lucebox draft.

.DESCRIPTION
    Launches llama-server.exe from the BeeLlama build with speculative decoding
    using either the BeeLlama DFlash draft model or the Lucebox draft model.
    The server binds to 127.0.0.1:8080 for LiteLLM proxy consumption.

.PARAMETER Mode
    Speculative decoding mode: "beellama" (default) or "lucebox".
    - beellama: Uses Qwen3.6-27B-DFlash-IQ4_XS.gguf (892MB, native DFlash support)
    - lucebox:  Uses dflash-draft-3.6-q4_k_m.gguf (1GB, Q4_K_M quant draft)

.PARAMETER GpuLayers
    Number of layers to offload to GPU. Default: 999 (all layers).

.PARAMETER ContextSize
    Context window size. Default: 32768.

.PARAMETER Threads
    CPU threads for the target model. Default: auto (8 for Ryzen 7 5800X).

.PARAMETER DraftThreads
    CPU threads for the draft model. Default: auto (8).

.PARAMETER DraftNMax
    Max draft tokens per speculation step. Default: 16.

.PARAMETER Parallel
    Number of parallel server slots. Default: 2.

.EXAMPLE
    .\start-speculative-server.ps1 -Mode beellama
    .\start-speculative-server.ps1 -Mode lucebox -DraftNMax 8
#>

param(
    [ValidateSet("beellama", "lucebox")]
    [string]$Mode = "beellama",

    [int]$GpuLayers = 999,
    [int]$ContextSize = 32768,
    [int]$Threads = 8,
    [int]$DraftThreads = 8,
    [int]$DraftNMax = 16,
    [int]$Parallel = 2,

    [double]$Temperature = 0.7,
    [double]$TopP = 0.9,
    [int]$TopK = 50,
    [double]$MinP = 0.05,
    [double]$PresencePenalty = 1.1,
    [double]$RepeatPenalty = 1.0,

    [switch]$ContinuousBatching,
    [switch]$Metrics,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

# --- Paths ---
$BeeLlamaDir = "C:\Users\Admin\PROJECTS\beellama-server"
$ServerExe = Join-Path $BeeLlamaDir "llama-server.exe"

$MainModel = "D:\MODELS\Qwen3.6-27B-Q3_K_M.gguf"

if ($Mode -eq "beellama") {
    $DraftModel = "D:\MODELS\Qwen3.6-27B-DFlash-IQ4_XS.gguf"
} else {
    $DraftModel = "C:\Users\Admin\PROJECTS\lucebox-models\draft\dflash-draft-3.6-q4_k_m.gguf"
}

# --- Validation ---
if (-not (Test-Path -LiteralPath $ServerExe)) {
    throw "llama-server.exe not found at '$ServerExe'"
}
if (-not (Test-Path -LiteralPath $MainModel)) {
    throw "Main model not found at '$MainModel'"
}
if (-not (Test-Path -LiteralPath $DraftModel)) {
    throw "Draft model not found at '$DraftModel'"
}

# --- Build arguments ---
$args = @(
    "-m", $MainModel,
    "--spec-draft", $DraftModel,
    "-ngl", $GpuLayers,
    "--spec-draft-ngl", $GpuLayers,
    "-c", $ContextSize,
    "-t", $Threads,
    "--spec-draft-threads", $DraftThreads,
    "--spec-draft-n-max", $DraftNMax,
    "-ctk", "q8_0",
    "-ctv", "q8_0",
    "-fa", "on",
    "--temp", $Temperature,
    "--top-p", $TopP,
    "--top-k", $TopK,
    "--min-p", $MinP,
    "--presence-penalty", $PresencePenalty,
    "--repeat-penalty", $RepeatPenalty,
    "--host", "127.0.0.1",
    "--port", "8080",
    "-np", $Parallel,
    "-b", "512",
    "-ub", "256",
    "--slots",
    "--webui"
)

if ($ContinuousBatching) { $args += "--cont-batching" }
if ($Metrics) { $args += "--metrics" }
if ($Verbose) { $args += "--verbose" }

# --- Info output ---
Write-Host ""
Write-Host "======================================================"
Write-Host "  Speculative Decoding Server (DFlash)"
Write-Host "======================================================"
Write-Host ""
Write-Host "Mode       : $Mode"
Write-Host "Main model : $MainModel"
Write-Host "Draft model: $DraftModel"
Write-Host "GPU layers : $GpuLayers (all)"
Write-Host "Context    : $ContextSize tokens"
Write-Host "Threads    : $Threads (main) / $DraftThreads (draft)"
Write-Host "Draft nMax : $DraftNMax"
Write-Host "Parallel   : $Parallel slots"
Write-Host "URL        : http://127.0.0.1:8080"
Write-Host ""
Write-Host "Command:"
Write-Host ($args -join " ")
Write-Host ""
Write-Host "Endpoints:"
Write-Host "  Health  : http://127.0.0.1:8080/health"
Write-Host "  Slots   : http://127.0.0.1:8080/slots"
Write-Host "  Chat    : http://127.0.0.1:8080/v1/chat/completions"
if ($Metrics) { Write-Host "  Metrics : http://127.0.0.1:8080/metrics" }
Write-Host ""
Write-Host "Starting server..."
Write-Host "------------------------------------------------------"
Write-Host ""

# --- Launch ---
Push-Location $BeeLlamaDir
try {
    & $ServerExe @args
}
finally {
    Pop-Location
}
