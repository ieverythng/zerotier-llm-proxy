[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Distro = "Ubuntu",
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30,
    [switch]$NoSkipBuild
)

$ErrorActionPreference = "Stop"

function Get-HermesDesktopState {
    param([string]$WslDistro)

    $linuxState = & wsl.exe -d $WslDistro -e bash -lc @'
browser=$(pgrep -f "/apps/desktop/release/linux-unpacked/Hermes --disable-setuid-sandbox$" | head -1)
backend=$(pgrep -f "hermes_cli.main serve --host 127.0.0.1 --port 0$" | head -1)
port=""
if [ -n "$backend" ]; then
    port=$(ss -ltnp 2>/dev/null | grep "pid=$backend," | sed -n 's/.*127\.0\.0\.1:\([0-9][0-9]*\).*/\1/p' | head -1)
fi
printf 'browser_pid=%s backend_pid=%s backend_port=%s\n' "$browser" "$backend" "$port"
'@

    $stateText = ($linuxState -join " ").Trim()
    $window = Get-Process -Name msrdc -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like "*Hermes*" -and $_.Responding } |
        Select-Object -First 1

    [pscustomobject]@{
        Ready = ($null -ne $window -and $stateText -match 'browser_pid=\d+ backend_pid=\d+ backend_port=\d+')
        Window = $window
        Linux = $stateText
    }
}

$hermesPath = (& wsl.exe -d $Distro -e bash -lc 'command -v hermes' | Select-Object -First 1).Trim()
if (-not $hermesPath) {
    throw "Hermes is not installed in WSL distro '$Distro'."
}

$copyMode = (& wsl.exe -d $Distro -e bash -lc @'
if grep -q 'rdp_allocate_shared_memory: Failed' /mnt/wslg/weston.log 2>/dev/null; then
    printf 'true'
else
    printf 'false'
fi
'@) -eq "true"

if ($copyMode) {
    Write-Warning "WSLg is in COPY MODE because shared-memory allocation failed. Hermes can launch, but rendering may be slow or unreliable. Close active WSL shells, then run 'wsl.exe --terminate $Distro' once to reset WSLg."
}

$state = Get-HermesDesktopState -WslDistro $Distro
if ($state.Ready) {
    Write-Host "Hermes Desktop is already ready: $($state.Linux)" -ForegroundColor Green
    exit 0
}

$arguments = @('-d', $Distro, '-e', $hermesPath, 'desktop')
if (-not $NoSkipBuild) {
    $arguments += '--skip-build'
}

$launcher = Start-Process -FilePath 'wsl.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
$stopwatch = [Diagnostics.Stopwatch]::StartNew()

do {
    Start-Sleep -Milliseconds 500
    $launcher.Refresh()
    $state = Get-HermesDesktopState -WslDistro $Distro
    if ($state.Ready) {
        $stopwatch.Stop()
        Write-Host ("Hermes Desktop ready in {0:N1}s: {1}" -f $stopwatch.Elapsed.TotalSeconds, $state.Linux) -ForegroundColor Green
        exit 0
    }
} while (-not $launcher.HasExited -and $stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds)

$stopwatch.Stop()
$recentLog = & wsl.exe -d $Distro -e bash -lc 'strings ~/.hermes/logs/desktop.log 2>/dev/null | tail -n 40'
if ($recentLog) {
    Write-Host "Recent Hermes desktop log:" -ForegroundColor Yellow
    $recentLog | ForEach-Object { Write-Host "  $_" }
}

$exitDetail = if ($launcher.HasExited) { "launcher exited with code $($launcher.ExitCode)" } else { "startup timed out" }
throw "Hermes Desktop did not become ready after $([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s ($exitDetail)."
