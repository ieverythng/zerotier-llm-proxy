# Start-Qwen36ZeroTierStack.ps1
#
# Backwards-compatible entry point. Start-WatsonStack.ps1 is the canonical
# generic launcher; this filename remains for existing benchmarks, docs, and
# scheduled tasks that still reference the historical Qwen3.6 name.

[CmdletBinding()]
param(
    [string]$LlamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server",
    [string]$LlamaScript = "scripts\start_profile.ps1",
    [string]$LlamaBinDir = "",
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Model = "qwen3.8",
    [string]$Profile = "",
    [int]$ContextSize = 100096,
    [int]$BatchSize = 0,
    [int]$UBatchSize = 0,
    [string]$ModelDirectory = "D:\MODELS",
    [string]$ModelPath = "",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ServedAlias = "",
    [string]$BackendKey = "llama.cpp",
    [switch]$Metrics,
    [switch]$SkipChatParsing,
    [switch]$SkipLlamaStart,
    [switch]$ReplaceLiteLLM,
    [switch]$NoHeadroom,
    [switch]$RouteHermesThroughHeadroom,
    [int]$HeadroomPort = 8787,
    [switch]$SkipHermesSync,
    [switch]$ForceHeadroomCompression,
    [switch]$EnableOracle,
    [switch]$NoOracle,
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [int]$Webchat2ApiPort = 9000
)

$ErrorActionPreference = "Stop"

if (-not $Profile) {
    if ($Model -eq "qwen3.8") {
        # The Qwen3.8 GGUF is served by the official CUDA profile at both
        # 65k and 100k allocations; never silently select the Qwen3.6
        # TurboQuant profile for this model.
        $Profile = "hermes-qwen38-100k"
    } else {
        $Profile = "hermes-qwen36-64k"
    }
}

if (-not $ServedAlias) {
    $ServedAlias = $Model
}

$canonicalLauncher = Join-Path $PSScriptRoot "Start-WatsonStack.ps1"
$launcherArgs = @(
    "-LlamaRepo", $LlamaRepo,
    "-LlamaScript", $LlamaScript,
    "-LlamaPort", $LlamaPort,
    "-LiteLLMPort", $LiteLLMPort,
    "-Model", $Model,
    "-ServedAlias", $ServedAlias,
    "-Profile", $Profile,
    "-ContextSize", $ContextSize,
    "-BatchSize", $BatchSize,
    "-UBatchSize", $UBatchSize,
    "-ModelDirectory", $ModelDirectory,
    "-BackendKey", $BackendKey,
    "-HeadroomPort", $HeadroomPort,
    "-Webchat2ApiPath", $Webchat2ApiPath,
    "-Webchat2ApiPort", $Webchat2ApiPort
)

if ($ModelPath) { $launcherArgs += @("-ModelPath", $ModelPath) }
if ($LlamaBinDir) { $launcherArgs += @("-LlamaBinDir", $LlamaBinDir) }
if ($Metrics) { $launcherArgs += "-Metrics" }
if ($SkipChatParsing) { $launcherArgs += "-SkipChatParsing" }
if ($SkipLlamaStart) { $launcherArgs += "-SkipLlamaStart" }
if ($ReplaceLiteLLM) { $launcherArgs += "-ReplaceLiteLLM" }
if ($NoHeadroom) { $launcherArgs += "-NoHeadroom" }
if ($RouteHermesThroughHeadroom) { $launcherArgs += "-RouteHermesThroughHeadroom" }
if ($SkipHermesSync) { $launcherArgs += "-SkipHermesSync" }
if ($ForceHeadroomCompression) { $launcherArgs += "-ForceHeadroomCompression" }
if ($EnableOracle) { $launcherArgs += "-EnableOracle" }
if ($NoOracle) { $launcherArgs += "-NoOracle" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $canonicalLauncher @launcherArgs
exit $LASTEXITCODE
