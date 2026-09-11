[CmdletBinding()]
param(
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$LiteLLMBaseUrl = "http://127.0.0.1:4000/v1",
    [string]$CodexProfilePath = "$env:USERPROFILE\.codex\qwen38-zerotier.config.toml",
    [string]$LlamaModel = "qwen3.8",
    [string]$CanonicalModel = "qwen3.8",
    [int]$ExpectedContextWindow = 0,
    [string]$ApiKey = "local-qwen36"
)

$ErrorActionPreference = "Stop"

function Get-TomlInt {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $match = [regex]::Match((Get-Content -LiteralPath $Path -Raw), "(?m)^\s*$([regex]::Escape($Key))\s*=\s*(\d+)\s*$")
    if ($match.Success) { return [int]$match.Groups[1].Value }
    return $null
}

$llama = Invoke-RestMethod -Uri "$($LlamaBaseUrl.TrimEnd('/'))/models" -TimeoutSec 10
$llamaEntry = @($llama.data | Where-Object { $_.id -eq $LlamaModel } | Select-Object -First 1)
if ($llamaEntry.Count -ne 1) { throw "llama.cpp did not expose '$LlamaModel'." }
$serverContext = [int]$llamaEntry[0].meta.n_ctx
if ($ExpectedContextWindow -eq 0) { $ExpectedContextWindow = $serverContext }

$headers = @{}
if ($ApiKey) { $headers.Authorization = "Bearer $ApiKey" }
$proxy = Invoke-RestMethod -Uri "$($LiteLLMBaseUrl.TrimEnd('/'))/models" -Headers $headers -TimeoutSec 10
$canonicalVisible = @($proxy.data | Where-Object { $_.id -eq $CanonicalModel }).Count -eq 1
if (-not $canonicalVisible) { throw "LiteLLM did not expose canonical model '$CanonicalModel'." }

$profileContext = Get-TomlInt -Path $CodexProfilePath -Key "model_context_window"
[pscustomobject]@{
    llama_model = $LlamaModel
    canonical_model = $CanonicalModel
    expected_context_window = $ExpectedContextWindow
    llama_context_window = $serverContext
    codex_profile_context_window = $profileContext
    codex_profile_path = $CodexProfilePath
    litellm_canonical_visible = $canonicalVisible
} | ConvertTo-Json -Compress

if ($serverContext -ne $ExpectedContextWindow) { throw "llama.cpp context mismatch: expected $ExpectedContextWindow, got $serverContext." }
if ($profileContext -and $profileContext -ne $ExpectedContextWindow) { throw "Codex profile context mismatch: expected $ExpectedContextWindow, got $profileContext." }
Write-Host "Qwen3.8 context mode check passed."
