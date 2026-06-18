param(
    [switch]$IncludeOracle,
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [int]$Webchat2ApiPort = 9000,
    [int]$DflashProxyPort = 18080
)

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Message) Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  [WARN] $Message" -ForegroundColor Yellow }

function Stop-PortOwner {
    param([int]$Port, [string]$Name)

    $connections = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Ok "No $Name listener on port $Port"
        return
    }

    foreach ($connection in $connections) {
        Write-Warn "Stopping $Name PID $($connection.OwningProcess) on port $Port"
        Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "       Stopping Lucebox DFlash Stack" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta

Write-Step "Stopping DFlash and llama.cpp"
Stop-Process -Name dflash_server,llama-server -Force -ErrorAction SilentlyContinue
Stop-PortOwner -Port $LlamaPort -Name "DFlash/llama.cpp"

Write-Step "Stopping LiteLLM"
Stop-PortOwner -Port $LiteLLMPort -Name "LiteLLM"

Write-Step "Stopping DFlash compatibility proxy"
Stop-PortOwner -Port $DflashProxyPort -Name "DFlash proxy"

if ($IncludeOracle) {
    Write-Step "Stopping webchat2api"
    wsl -e sh -lc "pkill -f 'webchat2api|main.py' 2>/dev/null || true" 2>$null
    Stop-PortOwner -Port $Webchat2ApiPort -Name "webchat2api"
}

Start-Sleep -Seconds 1
$remaining = Get-NetTCPConnection -State Listen -LocalPort @($LlamaPort, $DflashProxyPort, $LiteLLMPort) -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Warn "Some ports are still in use"
    $remaining | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize
} else {
    Write-Ok "Stack ports are clear"
}
