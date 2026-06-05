param(
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$LiteLLMBaseUrl = "http://127.0.0.1:4000/v1",
    [string]$CodexProfilePath = "$env:USERPROFILE\.codex\qwen36-zerotier.config.toml",
    [string]$Model = "qwen36-turbo-hermes",
    [int]$ExpectedContextWindow = 65536,
    [string]$ApiKey = "local-qwen36"
)

$ErrorActionPreference = "Stop"

function Invoke-JsonGet {
    param(
        [string]$Uri,
        [hashtable]$Headers = @{}
    )

    return Invoke-RestMethod -Uri $Uri -Headers $Headers -Method Get -TimeoutSec 10
}

function Get-TomlInt {
    param(
        [string]$Path,
        [string]$Key
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $text = Get-Content -LiteralPath $Path -Raw
    $match = [regex]::Match($text, "(?m)^\s*$([regex]::Escape($Key))\s*=\s*(\d+)\s*$")
    if (-not $match.Success) {
        return $null
    }

    return [int]$match.Groups[1].Value
}

$headers = @{}
if ($ApiKey) {
    $headers.Authorization = "Bearer $ApiKey"
}

$llamaModels = Invoke-JsonGet -Uri "$($LlamaBaseUrl.TrimEnd('/'))/models"
$llamaModel = $llamaModels.data | Where-Object { $_.id -eq $Model } | Select-Object -First 1
if (-not $llamaModel) {
    throw "llama.cpp did not expose model '$Model' at $LlamaBaseUrl/models."
}

$litellmModels = Invoke-JsonGet -Uri "$($LiteLLMBaseUrl.TrimEnd('/'))/models" -Headers $headers
$proxyModel = $litellmModels.data | Where-Object { $_.id -eq $Model } | Select-Object -First 1
if (-not $proxyModel) {
    throw "LiteLLM did not expose model '$Model' at $LiteLLMBaseUrl/models."
}

$serverContext = [int]$llamaModel.meta.n_ctx
$trainContext = [int]$llamaModel.meta.n_ctx_train
$profileContext = Get-TomlInt -Path $CodexProfilePath -Key "model_context_window"

[pscustomobject]@{
    model = $Model
    expected_context_window = $ExpectedContextWindow
    llama_context_window = $serverContext
    llama_train_context_window = $trainContext
    codex_profile_context_window = $profileContext
    codex_profile_path = $CodexProfilePath
    litellm_model_visible = $true
} | ConvertTo-Json

if ($serverContext -ne $ExpectedContextWindow) {
    throw "llama.cpp context mismatch: expected $ExpectedContextWindow, got $serverContext."
}

if ($profileContext -and $profileContext -ne $ExpectedContextWindow) {
    throw "Codex profile context mismatch: expected $ExpectedContextWindow, got $profileContext in $CodexProfilePath."
}

if (-not $profileContext) {
    Write-Warning "Codex profile context was not found at $CodexProfilePath."
}

Write-Host "Context mode check passed."

