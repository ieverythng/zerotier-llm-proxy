[CmdletBinding()]
param([int]$Port = 8787)

$ErrorActionPreference = "Stop"
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $listener) {
    throw "Headroom is not listening on port $Port."
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
[pscustomobject]@{
    Port = $Port
    ProcessId = $listener.OwningProcess
    Health = $health
} | Format-List
