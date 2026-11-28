[CmdletBinding()]
param(
    [int]$Port = 18787,
    [string]$Upstream = 'http://127.0.0.1:4000/v1',
    [string]$ExpectedModel = 'saluki27b'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $repoRoot 'scripts\windows\Start-HeadroomHermes.ps1'
$stopScript = Join-Path $repoRoot 'scripts\windows\Stop-HeadroomHermes.ps1'
$oldUserToken = [Environment]::GetEnvironmentVariable('HEADROOM_PROXY_TOKEN', 'User')
$oldProcessToken = $env:HEADROOM_PROXY_TOKEN
$headroomStartAttempted = $false
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$tokenBytes = New-Object byte[] 32
$rng.GetBytes($tokenBytes)
$testToken = -join ($tokenBytes | ForEach-Object { $_.ToString('x2') })
$rng.Dispose()

try {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Test port $Port is already in use."
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 8787 -ErrorAction SilentlyContinue) {
        throw 'Production Headroom is listening on port 8787. Stop it before running this isolated test; Headroom instances share the global memory store.'
    }

    [Environment]::SetEnvironmentVariable('HEADROOM_PROXY_TOKEN', $testToken, 'User')
    Remove-Item Env:HEADROOM_PROXY_TOKEN -ErrorAction SilentlyContinue

    $headroomStartAttempted = $true
    & $startScript -Port $Port -LiteLLMUpstream $Upstream

    $zeroTierIp = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.InterfaceAlias -like '*ZeroTier*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $zeroTierIp) {
        throw 'No ZeroTier IPv4 address is available for the non-loopback auth check.'
    }

    $modelsUri = "http://${zeroTierIp}:$Port/v1/models"
    $unauthorizedStatus = $null
    try {
        Invoke-WebRequest -Uri $modelsUri -TimeoutSec 5 | Out-Null
        $unauthorizedStatus = 200
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $unauthorizedStatus = [int]$_.Exception.Response.StatusCode
        }
        else {
            throw
        }
    }
    if ($unauthorizedStatus -ne 401) {
        throw "Unauthenticated non-loopback request returned HTTP $unauthorizedStatus; expected 401."
    }

    $models = Invoke-RestMethod -Uri $modelsUri -Headers @{ 'X-Headroom-Proxy-Token' = $testToken } -TimeoutSec 10
    $modelIds = @($models.data | ForEach-Object { $_.id })
    if ($modelIds -notcontains $ExpectedModel) {
        throw "Authenticated request did not reach LiteLLM model '$ExpectedModel'. Returned: $($modelIds -join ', ')"
    }

    Write-Host 'Headroom proxy auth contract passed: unauthenticated non-loopback denied; token-authenticated /v1/models reached LiteLLM.' -ForegroundColor Green
}
finally {
    if ($headroomStartAttempted) {
        try { & $stopScript -Port $Port } catch { }
    }

    if ($null -eq $oldUserToken) {
        [Environment]::SetEnvironmentVariable('HEADROOM_PROXY_TOKEN', $null, 'User')
    }
    else {
        [Environment]::SetEnvironmentVariable('HEADROOM_PROXY_TOKEN', $oldUserToken, 'User')
    }

    if ($null -eq $oldProcessToken) {
        Remove-Item Env:HEADROOM_PROXY_TOKEN -ErrorAction SilentlyContinue
    }
    else {
        $env:HEADROOM_PROXY_TOKEN = $oldProcessToken
    }
}
