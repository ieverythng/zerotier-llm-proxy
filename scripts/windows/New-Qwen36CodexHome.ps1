param(
    [string]$SourceCodexHome = "$env:USERPROFILE\.codex",
    [string]$TargetCodexHome = (Join-Path $env:TEMP "codex-qwen36-home"),
    [string]$BaseUrl = "http://10.88.140.94:4000/v1"
)

$ErrorActionPreference = "Stop"

if (Test-Path -LiteralPath $TargetCodexHome) {
    Remove-Item -LiteralPath $TargetCodexHome -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $TargetCodexHome | Out-Null

$sourceConfig = Join-Path $SourceCodexHome "config.toml"
$targetConfig = Join-Path $TargetCodexHome "config.toml"

if (Test-Path -LiteralPath $sourceConfig) {
    Copy-Item -LiteralPath $sourceConfig -Destination $targetConfig
} else {
    New-Item -ItemType File -Path $targetConfig | Out-Null
}

$text = Get-Content -Raw -LiteralPath $targetConfig
if ($null -eq $text) {
    $text = ""
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

$text = [regex]::Replace($text, "(?ms)\r?\n?\[model_providers\.qwen36-zerotier\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
$text = [regex]::Replace($text, "(?ms)\r?\n?\[profiles\.qwen36-zerotier\].*?(?=\r?\n\[[^\]]+\]|\z)", "")
$text = Set-Or-InsertTopLevel -Text $text -Key "model" -Value '"qwen36-turbo-hermes"'
$text = Set-Or-InsertTopLevel -Text $text -Key "model_provider" -Value '"qwen36-zerotier"'
$text = Set-Or-InsertTopLevel -Text $text -Key "model_context_window" -Value "65536"
$text = Set-Or-InsertTopLevel -Text $text -Key "model_max_output_tokens" -Value "8192"

$provider = @"

[model_providers.qwen36-zerotier]
name = "qwen36 via Windows ZeroTier LiteLLM"
base_url = "$BaseUrl"
wire_api = "responses"
"@

Set-Content -LiteralPath $targetConfig -Value ($text.TrimEnd() + $provider + "`r`n") -Encoding UTF8
Write-Output $TargetCodexHome
