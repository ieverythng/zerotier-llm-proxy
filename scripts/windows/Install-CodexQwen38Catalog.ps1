[CmdletBinding()]
param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$ProviderName = "qwen36-zerotier",
    [string]$BaseUrl = "http://10.88.140.94:4000/v1",
    [string]$ModelSlug = "qwen3.8",
    [string]$ProfileName = "qwen38-zerotier",
    [string]$CompatibilityProfileName = "qwen36-zerotier",
    [string[]]$LegacyProviderNames = @("qwen38-watson-bridge"),
    [switch]$SetDefault
)

$ErrorActionPreference = "Stop"

function Set-Or-InsertTopLevel {
    param([string]$Text, [string]$Key, [string]$Value)

    $line = "$Key = $Value"
    if ($Text -match "(?m)^$([regex]::Escape($Key))\s*=") {
        return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Key))\s*=.*$", $line, 1)
    }
    return "$line`r`n$Text"
}

$models = Invoke-RestMethod -Uri "$($LlamaBaseUrl.TrimEnd('/'))/models" -Method Get -TimeoutSec 10
$entry = @($models.data | Where-Object { $_.meta.n_ctx } | Select-Object -First 1)
if ($entry.Count -ne 1) {
    throw "Could not read meta.n_ctx from llama.cpp at $LlamaBaseUrl/models."
}
$contextWindow = [int]$entry[0].meta.n_ctx
if ($contextWindow -lt 8192) { throw "Invalid llama.cpp context window: $contextWindow" }

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
$configPath = Join-Path $CodexHome "config.toml"
$catalogDir = Join-Path $CodexHome "model-catalogs"
$catalogPath = Join-Path $catalogDir "qwen38-plus-bundled.json"
$profilePath = Join-Path $CodexHome "$ProfileName.config.toml"

if (-not (Test-Path -LiteralPath $configPath)) {
    New-Item -ItemType File -Force -Path $configPath | Out-Null
}

$configText = Get-Content -Raw -LiteralPath $configPath
$configText = Set-Or-InsertTopLevel -Text $configText -Key "model_catalog_json" -Value ('"' + $catalogPath.Replace('\', '/') + '"')

foreach ($legacyProviderName in $LegacyProviderNames) {
    if (-not $legacyProviderName -or $legacyProviderName -eq $ProviderName) { continue }
    $legacyPattern = "(?ms)\r?\n?\[model_providers\.$([regex]::Escape($legacyProviderName))\].*?(?=\r?\n\[[^\]]+\]|\z)"
    $configText = [regex]::Replace($configText, $legacyPattern, "")
}

# Keep the existing provider id so old profiles continue to work, but make the
# description truthful about the canonical public model name.
$providerBlock = @"

[model_providers.$ProviderName]
name = "qwen3.8 via Windows ZeroTier LiteLLM"
base_url = "$BaseUrl"
wire_api = "responses"
"@
$configText = [regex]::Replace($configText, "(?ms)\r?\n?\[model_providers\.$([regex]::Escape($ProviderName))\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
Set-Content -LiteralPath $configPath -Value ($configText.TrimEnd() + $providerBlock + "`r`n") -Encoding UTF8

$bundledJson = (& codex debug models --bundled 2>$null) -join "`n"
if (-not $bundledJson.TrimStart().StartsWith('{')) {
    throw "codex debug models --bundled did not return JSON."
}
$catalog = $bundledJson | ConvertFrom-Json
# Responses Lite is an OpenAI-hosted transport contract. It moves Codex tools
# into an `additional_tools` input item that llama.cpp and Ollama's standard
# Responses endpoints do not consume. Start from a non-Lite catalog entry so
# local providers receive ordinary top-level tool definitions.
$baseTemplate = $catalog.models | Where-Object { -not $_.use_responses_lite } | Select-Object -First 1
if (-not $baseTemplate) {
    throw "The bundled Codex catalog has no standard Responses model template."
}
$base = $baseTemplate | ConvertTo-Json -Depth 20 | ConvertFrom-Json
$base.slug = $ModelSlug
$base.display_name = "Qwen3.8 (Watson)"
$base.description = "Windows-hosted Qwen3.8 served through ZeroTier LiteLLM; context is read from live llama.cpp metadata."
$base.default_reasoning_level = "low"
$base.supported_reasoning_levels = @(
    [pscustomobject]@{ effort = "low"; description = "Fast local responses" },
    [pscustomobject]@{ effort = "medium"; description = "Balanced local reasoning" }
)
$base.priority = 1
$base.supported_in_api = $true
$base.use_responses_lite = $false
$base.additional_speed_tiers = @()
$base.service_tiers = @()
$base.availability_nux = $null
$base.context_window = $contextWindow
$base.max_context_window = $contextWindow
$catalog.models = @($catalog.models | Where-Object { $_.slug -notin @($ModelSlug, "qwen36-turbo-hermes") }) + $base
New-Item -ItemType Directory -Force -Path $catalogDir | Out-Null
$catalog | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $catalogPath -Encoding UTF8

$profile = @"
model = "$ModelSlug"
model_provider = "$ProviderName"
model_context_window = $contextWindow
model_max_output_tokens = 8192
"@
Set-Content -LiteralPath $profilePath -Value ($profile.TrimStart() + "`r`n") -Encoding UTF8

# Keep an existing compatibility profile's context truthful without changing
# the model slug it was created for.
$compatibilityProfilePath = Join-Path $CodexHome "$CompatibilityProfileName.config.toml"
if ($CompatibilityProfileName -and (Test-Path -LiteralPath $compatibilityProfilePath) -and
    ([IO.Path]::GetFullPath($compatibilityProfilePath) -ne [IO.Path]::GetFullPath($profilePath))) {
    $compatText = Get-Content -Raw -LiteralPath $compatibilityProfilePath
    if ($compatText -match "(?m)^model_context_window\s*=") {
        $compatText = [regex]::Replace($compatText, "(?m)^model_context_window\s*=.*$", "model_context_window = $contextWindow", 1)
    }
    Set-Content -LiteralPath $compatibilityProfilePath -Value $compatText -Encoding UTF8
}

if ($SetDefault) {
    $defaultText = Get-Content -Raw -LiteralPath $configPath
    $defaultText = Set-Or-InsertTopLevel -Text $defaultText -Key "model" -Value ('"' + $ModelSlug + '"')
    $defaultText = Set-Or-InsertTopLevel -Text $defaultText -Key "model_provider" -Value ('"' + $ProviderName + '"')
    $defaultText = Set-Or-InsertTopLevel -Text $defaultText -Key "model_context_window" -Value $contextWindow
    $defaultText = Set-Or-InsertTopLevel -Text $defaultText -Key "model_max_output_tokens" -Value "8192"
    Set-Content -LiteralPath $configPath -Value $defaultText -Encoding UTF8
}

[pscustomobject]@{
    model = $ModelSlug
    profile = $profilePath
    catalog = $catalogPath
    context_window = $contextWindow
    base_url = $BaseUrl
    default_updated = [bool]$SetDefault
} | ConvertTo-Json -Compress
