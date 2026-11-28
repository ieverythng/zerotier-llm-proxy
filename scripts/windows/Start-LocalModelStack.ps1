[CmdletBinding()]
param(
    [switch]$Serve,
    [switch]$NoUi,
    [switch]$Direct
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repoRoot '.runtime\local-model-deps\tui-rich15.0.0\Scripts\python.exe'
$supervisor = Join-Path $PSScriptRoot 'local_model_supervisor.py'
$state = Join-Path $repoRoot '.runtime\local-stack'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The terminal runtime is not staged. Run scripts\windows\Install-LocalModelRuntime.ps1 first.'
}
if (-not (Test-Path -LiteralPath $supervisor -PathType Leaf)) { throw 'Local model supervisor is missing.' }
$modeArguments = @()
if ($Direct) { $modeArguments += '--direct' }
Push-Location $repoRoot
try {
    if ($Serve) {
        Write-Host 'Supervising Watson Local. Ctrl+C stops this isolated stack and its owned model.' -ForegroundColor Cyan
        & $python -B $supervisor --serve @modeArguments
        if ($LASTEXITCODE -ne 0) { throw "Local supervisor stopped with exit code $LASTEXITCODE; inspect .runtime\local-stack logs." }
        return
    }

    if (-not $NoUi) {
        # The terminal is a persistent controller, not a child of the inference
        # workload. Pick a model/context before explicitly starting it.
        $uiArguments = @('-B', '-m', 'local_stack', 'ui')
        if ($Direct) { $uiArguments += '--direct' }
        & $python @uiArguments
        if ($LASTEXITCODE -ne 0) { throw "Terminal selector exited with code $LASTEXITCODE." }
        return
    }

    & $python -B $supervisor --check @modeArguments
    $alreadyReady = $LASTEXITCODE -eq 0
    if (-not $alreadyReady) {
        # Never adopt a listener merely because it answers health requests. The
        # Python supervisor additionally verifies all isolated/legacy ports.
        New-Item -ItemType Directory -Path $state -Force | Out-Null
        $stdout = Join-Path $state 'supervisor.stdout.log'
        $stderr = Join-Path $state 'supervisor.stderr.log'
        $arguments = @('-B', ('"' + $supervisor + '"'), '--serve') + $modeArguments
        $process = Start-Process -FilePath $python -WorkingDirectory $repoRoot -ArgumentList $arguments `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            $process.Refresh()
            if ($process.HasExited) {
                throw "Local stack startup exited (code $($process.ExitCode)); inspect .runtime\local-stack\supervisor.stderr.log. Nothing unrelated was stopped."
            }
            & $python -B $supervisor --check @modeArguments
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $ready) {
            # The supervisor owns its children and its Windows job. Do not kill
            # any arbitrary port owner as a timeout recovery operation.
            throw 'The supervised stack is still starting. Inspect its logs; no unrelated processes were stopped.'
        }
    }
    Write-Host 'Watson Local is ready. The supervised stack is running without a selector.' -ForegroundColor Green
}
finally {
    Pop-Location
}
