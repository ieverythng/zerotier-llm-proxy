[CmdletBinding()]
param(
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [int]$HeadroomPort = 8787,
    [switch]$IncludeOracle,
    [int]$Webchat2ApiPort = 9000
)

$ErrorActionPreference = "Stop"

function Stop-Listener {
    param([int]$Port, [string]$Name)

    $processIds = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "$Name stopped on port $Port"
}

Stop-Listener -Port $HeadroomPort -Name "Headroom"
Stop-Listener -Port $LiteLLMPort -Name "LiteLLM"
Stop-Listener -Port $LlamaPort -Name "llama.cpp"

if ($IncludeOracle) {
    Stop-Listener -Port $Webchat2ApiPort -Name "webchat2api"
    wsl.exe -d Ubuntu -- bash -lc "pkill -f 'webchat2api.*main.py' || true"
}
