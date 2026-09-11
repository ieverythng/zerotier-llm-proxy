param(
    [ValidateSet("balanced-1024-512")]
    [string]$Candidate = "balanced-1024-512",
    [switch]$NoStopFirst,
    [switch]$NoVerify,
    [switch]$NoHeadroomRoute
)

$ErrorActionPreference = "Stop"

$stackScript = Join-Path $PSScriptRoot "Start-WatsonStack.ps1"
$stopScript = Join-Path $PSScriptRoot "Stop-Qwen36ZeroTierStack.ps1"
$verifyScript = Join-Path $PSScriptRoot "Test-Qwen36ContextMode.ps1"

if (-not (Test-Path -LiteralPath $stackScript)) {
    throw "Canonical stack launcher not found: $stackScript"
}

if (-not (Test-Path -LiteralPath $stopScript)) {
    throw "Stack stop script not found: $stopScript"
}

$profile = switch ($Candidate) {
    "balanced-1024-512" {
        [pscustomobject]@{
            ContextSize = 65536
            BatchSize = 1024
            UBatchSize = 512
            Description = "Autoresearch balanced candidate: +~5.1% fixed-corpus prefill, route/workflow gates passed, not default-promoted."
        }
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "       Qwen36 Autoresearch Candidate Launcher" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host ("Candidate : {0}" -f $Candidate) -ForegroundColor Cyan
Write-Host ("Context   : {0}" -f $profile.ContextSize)
Write-Host ("Batch     : {0}" -f $profile.BatchSize)
Write-Host ("UBatch    : {0}" -f $profile.UBatchSize)
Write-Host ("Note      : {0}" -f $profile.Description)
Write-Host ""

if (-not $NoStopFirst) {
    Write-Host "Stopping current stack before candidate launch so batch/ubatch changes cannot be skipped..." -ForegroundColor Yellow
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript
    if ($LASTEXITCODE -ne 0) {
        throw "Stack stop failed with exit code $LASTEXITCODE."
    }
    Start-Sleep -Seconds 3
}
else {
    Write-Host "NoStopFirst set: relying on current launcher health logic. Use only when you know llama is not already running with different batch/ubatch." -ForegroundColor Yellow
}

$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $stackScript,
    "-ContextSize", $profile.ContextSize,
    "-BatchSize", $profile.BatchSize,
    "-UBatchSize", $profile.UBatchSize,
    "-Metrics",
    "-ReplaceLiteLLM"
)

if (-not $NoHeadroomRoute) {
    $args += "-RouteHermesThroughHeadroom"
}

& powershell.exe @args
if ($LASTEXITCODE -ne 0) {
    throw "Candidate stack launch failed with exit code $LASTEXITCODE."
}

if (-not $NoVerify) {
    if (-not (Test-Path -LiteralPath $verifyScript)) {
        throw "Context verification script not found: $verifyScript"
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript -ExpectedContextWindow $profile.ContextSize
    if ($LASTEXITCODE -ne 0) {
        throw "Context verification failed with exit code $LASTEXITCODE."
    }

    $process = Get-CimInstance Win32_Process -Filter "Name = 'llama-server.exe'" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $process) {
        throw "Candidate launch verification failed: llama-server.exe is not running."
    }

    $commandLine = [string]$process.CommandLine
    $batchPattern = '(?:^|\s)-b\s+' + $profile.BatchSize + '(?=\s|$)'
    $ubatchPattern = '(?:^|\s)-ub\s+' + $profile.UBatchSize + '(?=\s|$)'
    if ($commandLine -notmatch $batchPattern -or $commandLine -notmatch $ubatchPattern) {
        throw "Candidate launch verification failed: llama command line does not contain expected batch/ubatch. CommandLine=$commandLine"
    }
    Write-Host "Candidate launch verified from llama command line." -ForegroundColor Green
}

exit 0
