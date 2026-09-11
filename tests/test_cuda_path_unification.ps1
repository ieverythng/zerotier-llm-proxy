$ErrorActionPreference = 'Stop'

$repo = Split-Path $PSScriptRoot -Parent
$canonical = 'C:\Users\Admin\PROJECTS\llama-b10621-win-cuda133'
$itrader = 'C:\Users\Admin\PROJECTS\iTRADER\itrader-azr'

if (-not (Test-Path (Join-Path $canonical 'llama-server.exe'))) {
    throw "Canonical CUDA build is missing: $canonical"
}
if (-not (Test-Path (Join-Path $canonical 'ggml-cuda.dll'))) {
    throw "Canonical CUDA backend is missing: $canonical"
}
if ([Environment]::GetEnvironmentVariable('LLAMA_CPP_BIN_DIR', 'User') -ne $canonical) {
    throw 'User-scoped LLAMA_CPP_BIN_DIR is not pinned to the canonical CUDA build.'
}

$itraderScript = Get-Content (Join-Path $itrader 'tools\start_llama_server.ps1') -Raw
$itraderCmd = Get-Content (Join-Path $itrader 'tools\start_llama_server.cmd') -Raw
$itraderBgCmd = Get-Content (Join-Path $itrader 'tools\start_llama_server_bg.cmd') -Raw
$llamaForeground = Get-Content 'C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_llama_server.ps1' -Raw
$profiles = Get-Content 'C:\Users\Admin\PROJECTS\llama-cpp-server\profiles\llama-profiles.json' -Raw
$sweep = Get-Content (Join-Path $repo 'scripts\windows\Invoke-QwenKvCacheSweep.ps1') -Raw

foreach ($text in @($itraderScript, $itraderCmd, $itraderBgCmd, $sweep)) {
    if ($text -notmatch [regex]::Escape($canonical)) {
        throw "Expected canonical CUDA build path in one active launcher/configuration."
    }
}
if ($profiles -notmatch 'llama-b10621-win-cuda133') {
    throw 'llama-cpp-server profiles do not reference the canonical CUDA build.'
}

foreach ($text in @($itraderScript, $itraderCmd, $itraderBgCmd)) {
    if ($text -match 'llama-bin-win-cuda13-b8604') {
        throw 'iTRADER still defaults to its obsolete private CUDA bundle.'
    }
}

if ($profiles -match '"defaults"\s*:\s*\{[^}]*llama-cpp-turboquant') {
    throw 'llama-cpp-server default profile still points to TurboQuant.'
}

foreach ($text in @($llamaForeground, $itraderScript)) {
    foreach ($token in @('$probe.Refresh()', '$null -ne $exitCode', 'IsNullOrWhiteSpace($probeOutput)')) {
        if ($text -notlike "*$token*") {
            throw "CUDA preflight regression guard is missing: $token"
        }
    }
}

Write-Output 'PASS: active iTRADER and llama.cpp profile paths resolve to the canonical CUDA build.'
