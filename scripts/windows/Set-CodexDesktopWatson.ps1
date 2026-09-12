[CmdletBinding(DefaultParameterSetName = "Enable")]
param(
    # Never default to the primary Codex home. Desktop local-model routing is
    # global within one Codex home, so changing ~/.codex breaks hosted models.
    [string]$CodexHome = "$env:USERPROFILE\.codex-watson",
    [string]$BaseUrl = "http://127.0.0.1:4000/v1",
    [string]$ModelSlug = "qwen3.8",
    [string]$DefaultModel = "gpt-5.6-sol",
    [switch]$SkipBackup,
    [Parameter(ParameterSetName = "Restore", Mandatory)]
    [switch]$Restore
)

$ErrorActionPreference = "Stop"
$configPath = Join-Path $CodexHome "config.toml"
$backupPath = Join-Path $CodexHome "config.before-watson-router.toml"

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}

function Set-OrInsertTopLevel {
    param([string]$Text, [string]$Key, [string]$Value)

    $line = "$Key = $Value"
    if ($Text -match "(?m)^$([regex]::Escape($Key))\s*=") {
        return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Key))\s*=.*$", $line, 1)
    }
    return "$line`r`n$Text"
}

function Remove-TopLevel {
    param([string]$Text, [string]$Key)

    return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Key))\s*=.*\r?\n?", "", 1)
}

if ($Restore) {
    if (-not (Test-Path -LiteralPath $backupPath)) {
        throw "No Watson desktop backup exists at '$backupPath'."
    }
    Copy-Item -LiteralPath $backupPath -Destination $configPath -Force
    Remove-Item -LiteralPath $backupPath -Force
    Write-Output "Restored the pre-Watson Codex configuration."
    return
}

if (-not (Test-Path -LiteralPath $configPath)) { throw "Codex config not found: $configPath" }

$configText = Get-Content -Raw -LiteralPath $configPath
$catalogMatch = [regex]::Match($configText, '(?m)^model_catalog_json\s*=\s*"([^"]+)"')
if (-not $catalogMatch.Success) {
    throw "Install the Watson catalog before enabling desktop mode."
}
$catalogPath = $catalogMatch.Groups[1].Value -replace '/', '\'
if (-not (Test-Path -LiteralPath $catalogPath)) { throw "Model catalog not found: $catalogPath" }
$catalog = Get-Content -Raw -LiteralPath $catalogPath | ConvertFrom-Json
$model = @($catalog.models | Where-Object { $_.slug -eq $ModelSlug })
if ($model.Count -ne 1 -or -not $model[0].supported_in_api -or $model[0].use_responses_lite) {
    throw "The '$ModelSlug' catalog entry is not desktop-safe. Re-run Install-CodexQwen38WatsonProfile.ps1."
}

if (-not $SkipBackup -and -not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath
}

# This is the same public configuration seam used by `ollama launch chatgpt`:
# API-mode models use a top-level base URL and must not pin model_provider.
$configText = Remove-TopLevel -Text $configText -Key "model_provider"
$configText = Set-OrInsertTopLevel -Text $configText -Key "openai_base_url" -Value ('"' + $BaseUrl.TrimEnd('/') + '"')
$configText = Set-OrInsertTopLevel -Text $configText -Key "model" -Value ('"' + $DefaultModel + '"')
$configText = Remove-TopLevel -Text $configText -Key "model_context_window"
$configText = Remove-TopLevel -Text $configText -Key "model_max_output_tokens"
Write-Utf8NoBom -Path $configPath -Text ($configText.TrimEnd() + "`r`n")

Write-Output "Watson-enabled desktop mode configured with hosted default '$DefaultModel' and local model '$ModelSlug'."
Write-Output "Restore later with: .\scripts\windows\Set-CodexDesktopWatson.ps1 -Restore"
