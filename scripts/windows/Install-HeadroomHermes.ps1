[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot ".runtime\headroom"
$venv = Join-Path $runtimeRoot ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    & $Python -m venv $venv
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install --upgrade "headroom-ai[proxy,ml,code]"
if ($LASTEXITCODE -ne 0) {
    throw "Headroom dependency installation failed."
}

Write-Host "Headroom installed at $venv" -ForegroundColor Green
