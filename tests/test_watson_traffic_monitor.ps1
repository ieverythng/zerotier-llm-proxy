$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$monitor = Join-Path $repo 'scripts\windows\Watch-WatsonTraffic.ps1'
$status = Join-Path $repo 'scripts\windows\Get-HeadroomHermesStatus.ps1'
$launcher = Join-Path $repo 'scripts\windows\Start-WatsonStack.ps1'

foreach ($path in @($monitor, $status, $launcher)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required script is missing: $path" }
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
}

$monitorSource = Get-Content -LiteralPath $monitor -Raw
$statusSource = Get-Content -LiteralPath $status -Raw
$launcherSource = Get-Content -LiteralPath $launcher -Raw

if ($monitorSource -match '4000/health') {
    throw 'Traffic monitor must not call LiteLLM /health because that endpoint performs model inference.'
}
if ($monitorSource -match 'Clear-Host') {
    throw 'Traffic monitor must render an atomic frame instead of using Clear-Host.'
}
foreach ($rendererToken in @('Write-DashboardFrame', 'FrameWidth', 'Demo')) {
    if ($monitorSource -notlike "*$rendererToken*") {
        throw "Traffic monitor is missing stable-renderer support: $rendererToken"
    }
}
if ($statusSource -match '4000/health') {
    throw 'Headroom status must use passive LiteLLM discovery instead of inference-backed /health.'
}
if ($statusSource -notmatch '4000/v1/models') {
    throw 'Headroom status is missing the passive LiteLLM /v1/models check.'
}
if ($launcherSource -notmatch 'SkipCoherenceCheck') {
    throw 'Launcher must expose an explicit opt-out for its real-generation coherence gate.'
}
if ($launcherSource -notmatch 'real model generation') {
    throw 'Launcher must tell operators that the coherence gate raises GPU utilization.'
}

$preview = & $monitor -Once -NoClear -Demo -FrameWidth 72
$previewLines = @(([string]$preview) -split "`r?`n")
$tooWide = @($previewLines | Where-Object Length -gt 71)
if ($tooWide) {
    throw "Narrow traffic monitor preview wrapped past 71 columns: $($tooWide[0])"
}
foreach ($label in @('WATSON TRAFFIC', 'PIPELINE', 'RECENT TASKS')) {
    if ($preview -notmatch [regex]::Escape($label)) {
        throw "Narrow traffic monitor preview is missing '$label'."
    }
}
$compactPreview = & $monitor -Once -NoClear -Demo -FrameWidth 48
$compactLines = @(([string]$compactPreview) -split "`r?`n")
if (@($compactLines | Where-Object Length -gt 47)) {
    throw 'Compact traffic monitor preview wrapped past 47 columns.'
}
foreach ($label in @('llama.cpp:8080 UP', 'hermes[wsl:17876] -> Headroom:8787')) {
    if ($compactPreview -notmatch [regex]::Escape($label)) {
        throw "Compact traffic monitor preview truncated '$label'."
    }
}

'PASS: observability paths are passive and intentional generation is explicit.'
