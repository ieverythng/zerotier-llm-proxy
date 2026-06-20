param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$BaseUrl = "http://10.88.140.94:18080/v1",
    [int]$ContextWindow = 65536,
    [switch]$SetDefault
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
$ConfigPath = Join-Path $CodexHome "config.toml"
$ProfilePath = Join-Path $CodexHome "qwen36-zerotier.config.toml"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    New-Item -ItemType File -Force -Path $ConfigPath | Out-Null
}

$configText = Get-Content -Raw -LiteralPath $ConfigPath
if ($null -eq $configText) {
    $configText = ""
}

$cleanedConfigText = [regex]::Replace($configText, "(?ms)\r?\n?\[model_providers\.qwen36-zerotier\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
$cleanedConfigText = [regex]::Replace($cleanedConfigText, "(?ms)\r?\n?\[profiles\.qwen36-zerotier\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
if ($cleanedConfigText -ne $configText) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $cleanupBackup = "$ConfigPath.backup-cleanup-$timestamp"
    Copy-Item -LiteralPath $ConfigPath -Destination $cleanupBackup
    Set-Content -LiteralPath $ConfigPath -Value ($cleanedConfigText.TrimEnd() + "`r`n") -Encoding UTF8
    Write-Host "Removed legacy qwen36 tables from $ConfigPath"
    Write-Host "Cleanup backup: $cleanupBackup"
}

function Set-Or-InsertTopLevel {
    param(
        [string]$Text,
        [string]$Key,
        [string]$Value
    )

    $line = "$Key = $Value"
    if ($Text -match "(?m)^$([regex]::Escape($Key))\s*=") {
        return [regex]::Replace($Text, "(?m)^$([regex]::Escape($Key))\s*=.*$", $line, 1)
    }

    return "$line`r`n$Text"
}

if ($SetDefault) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$ConfigPath.backup-$timestamp"
    Copy-Item -LiteralPath $ConfigPath -Destination $backup
    $text = Get-Content -Raw -LiteralPath $ConfigPath
    $text = Set-Or-InsertTopLevel -Text $text -Key "model" -Value '"qwen36-turbo-hermes"'
    $text = Set-Or-InsertTopLevel -Text $text -Key "model_provider" -Value '"qwen36-zerotier"'
    $text = Set-Or-InsertTopLevel -Text $text -Key "model_context_window" -Value $ContextWindow
    $text = Set-Or-InsertTopLevel -Text $text -Key "model_max_output_tokens" -Value "8192"
    Set-Content -LiteralPath $ConfigPath -Value $text -Encoding UTF8
    Write-Host "Updated default config: $ConfigPath"
    Write-Host "Backup: $backup"
}

$providerBlock = @"

[model_providers.qwen36-zerotier]
name = "qwen36 Lucebox via Windows ZeroTier"
base_url = "$BaseUrl"
wire_api = "responses"
"@

$configText = Get-Content -Raw -LiteralPath $ConfigPath
if ($null -eq $configText) {
    $configText = ""
}
$configText = [regex]::Replace($configText, "(?ms)\r?\n?\[model_providers\.qwen36-zerotier\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
Set-Content -LiteralPath $ConfigPath -Value ($configText.TrimEnd() + $providerBlock + "`r`n") -Encoding UTF8
Write-Host "Registered provider in Codex config: $ConfigPath"

$profileBlock = @"
model = "qwen36-turbo-hermes-spec"
model_provider = "qwen36-zerotier"
model_context_window = $ContextWindow
model_max_output_tokens = 8192
"@

Set-Content -LiteralPath $ProfilePath -Value ($profileBlock.TrimStart() + "`r`n") -Encoding UTF8
Write-Host "Installed selectable Codex profile: $ProfilePath"
