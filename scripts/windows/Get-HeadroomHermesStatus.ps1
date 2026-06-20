[CmdletBinding()]
param([int]$Port = 8787)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:$Port"
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 5
} catch {
    throw "Headroom health endpoint is unavailable on port $Port."
}
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
$memory = $health.checks.memory
if (-not $memory.enabled -or -not $memory.ready) {
    throw "Headroom memory backend is not ready."
}

$models = Invoke-RestMethod -Uri "$baseUrl/v1/models" -TimeoutSec 10
if (-not $models.data -or $models.data.Count -eq 0) {
    throw "Headroom cannot reach the LiteLLM OpenAI-compatible upstream."
}

try {
    Invoke-WebRequest -Uri "$baseUrl/v1/retrieve" -Method Post -ContentType "application/json" -Body '{"hash":"health-check"}' -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
    throw "Headroom retrieval health check returned an unexpected success."
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -ne 404) {
        throw "Headroom retrieval endpoint is not healthy. Expected HTTP 404 for a missing hash; got $statusCode."
    }
}

[pscustomobject]@{
    Port = $Port
    ProcessId = if ($listener) { $listener.OwningProcess } else { $null }
    Memory = $memory
    Models = @($models.data | ForEach-Object { $_.id })
    Health = $health.status
} | Format-List
