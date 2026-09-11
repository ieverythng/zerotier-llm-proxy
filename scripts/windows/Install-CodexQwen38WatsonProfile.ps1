[CmdletBinding()]
param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$BaseUrl = "http://127.0.0.1:4000/v1",
    [string]$ProviderName = "qwen38-watson",
    [string]$ProfileName = "qwen38-watson"
)

$ErrorActionPreference = "Stop"

$installer = Join-Path $PSScriptRoot "Install-CodexQwen38Catalog.ps1"
& $installer `
    -CodexHome $CodexHome `
    -LlamaBaseUrl $LlamaBaseUrl `
    -BaseUrl $BaseUrl `
    -ProviderName $ProviderName `
    -ModelSlug "qwen3.8" `
    -ProfileName $ProfileName `
    -CompatibilityProfileName "qwen38-zerotier"
