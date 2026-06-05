param(
    [ValidateSet("65536", "98304", "131072")]
    [string]$ContextWindow = "65536",
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$BaseUrl = "http://10.88.140.94:4000/v1",
    [switch]$SkipCodexInstall,
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"

$context = [int]$ContextWindow
$startScript = Join-Path $PSScriptRoot "Start-Qwen36ZeroTierStack.ps1"
$installScript = Join-Path $PSScriptRoot "Install-CodexQwen36Config.ps1"
$verifyScript = Join-Path $PSScriptRoot "Test-Qwen36ContextMode.ps1"

if (-not $SkipRestart) {
    $llamaStopScript = "C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\stop_llama_server.ps1"
    if (Test-Path -LiteralPath $llamaStopScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $llamaStopScript -Port 8080 -Preset q3
    }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -ContextSize $context -Metrics
}

if (-not $SkipCodexInstall) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript `
        -CodexHome $CodexHome `
        -BaseUrl $BaseUrl `
        -ContextWindow $context
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript `
    -ExpectedContextWindow $context `
    -CodexProfilePath (Join-Path $CodexHome "qwen36-zerotier.config.toml")

Write-Host ("Qwen36 context mode is ready: {0}" -f $context)

