[CmdletBinding()]
param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runtimeRoot = Join-Path $repoRoot '.runtime\litellm'
$venv = Join-Path $runtimeRoot '.venv'
$pythonExe = Join-Path $venv 'Scripts\python.exe'
$liteLLMExe = Join-Path $venv 'Scripts\litellm.exe'
$requirements = Join-Path $repoRoot 'requirements-litellm.txt'

if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Pinned LiteLLM requirements are missing: $requirements"
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project LiteLLM environment using '$Python'." }
}

& $pythonExe -m pip install --disable-pip-version-check --no-cache-dir --upgrade -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Pinned LiteLLM proxy installation failed.' }
if (-not (Test-Path -LiteralPath $liteLLMExe -PathType Leaf)) {
    throw "LiteLLM proxy executable was not created: $liteLLMExe"
}

$version = (& $pythonExe -c 'from importlib.metadata import version; print(version("litellm"))' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne '1.104.2') {
    throw "Project LiteLLM version check failed (expected 1.104.2, got '$version')."
}
& $liteLLMExe --help *> $null
if ($LASTEXITCODE -ne 0) { throw 'LiteLLM proxy CLI import check failed.' }

Write-Host "Watson LiteLLM 1.104.2 proxy runtime is ready at $venv" -ForegroundColor Green
