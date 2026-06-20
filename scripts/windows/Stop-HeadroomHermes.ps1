[CmdletBinding()]
param([int]$Port = 8787)

$ErrorActionPreference = "Stop"
Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Write-Host "Headroom stopped on port $Port"
