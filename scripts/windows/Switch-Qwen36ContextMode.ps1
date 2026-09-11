param(
    [ValidateSet("65536", "98304", "100096", "131072")]
    [string]$ContextWindow = "100096",
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$BaseUrl = "http://10.88.140.94:4000/v1",
    [switch]$SkipCodexInstall,
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"

$context = [int]$ContextWindow
$startScript = Join-Path $PSScriptRoot "Start-WatsonStack.ps1"
$installScript = Join-Path $PSScriptRoot "Install-CodexQwen38Catalog.ps1"
$verifyScript = Join-Path $PSScriptRoot "Test-Qwen38ContextMode.ps1"
$hermesMemoryScript = Join-Path $PSScriptRoot "Set-HermesHeadroomMemory.ps1"

function Invoke-PowerShellScript {
    param([object[]]$ArgumentList)

    & powershell.exe @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell child process failed with exit code $LASTEXITCODE."
    }
}

if (-not $SkipRestart) {
    $llamaStopScript = "C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\stop_llama_server.ps1"
    if (Test-Path -LiteralPath $llamaStopScript) {
        Invoke-PowerShellScript -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $llamaStopScript, '-Port', 8080, '-Preset', 'q3')
    }

    Invoke-PowerShellScript -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript, '-ContextSize', $context, '-Metrics')
}

if (-not $SkipCodexInstall) {
    Invoke-PowerShellScript -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installScript,
        '-CodexHome', $CodexHome,
        '-BaseUrl', $BaseUrl,
        '-ContextWindow', $context
    )
}

Invoke-PowerShellScript -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $hermesMemoryScript,
    '-ContextWindow', $context
)

Invoke-PowerShellScript -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $verifyScript,
    '-ExpectedContextWindow', $context,
    '-LlamaModel', 'qwen3.8',
    '-CodexProfilePath', (Join-Path $CodexHome "qwen38-zerotier.config.toml")
)

Write-Host ("Qwen3.8 context mode is ready: {0}" -f $context)
