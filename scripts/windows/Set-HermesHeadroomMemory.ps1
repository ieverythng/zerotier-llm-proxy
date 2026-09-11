[CmdletBinding()]
param(
    [ValidateRange(0, 1048576)]
    [int]$ContextWindow = 0,
    [string]$LlamaBaseUrl = "http://127.0.0.1:8080/v1",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ProjectId = "watson-global",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$UserId = "juanbeck",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$hermes = "/home/juanbeck/.local/bin/hermes"

if ($ContextWindow -eq 0) {
    $models = Invoke-RestMethod -Uri "$($LlamaBaseUrl.TrimEnd('/'))/models" -Method Get -TimeoutSec 10
    $entry = @($models.data | Where-Object { $_.meta.n_ctx } | Select-Object -First 1)
    if ($entry.Count -ne 1) {
        throw "Could not derive context window from llama.cpp at $LlamaBaseUrl/models. Pass -ContextWindow explicitly."
    }
    $ContextWindow = [int]$entry[0].meta.n_ctx
}

function Invoke-Hermes {
    param([string[]]$Arguments)

    & wsl.exe -d Ubuntu -- $hermes @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

# Headroom's project storage is selected per request. A stable project ID makes
# memory shared across Discord channels and other Hermes surfaces while the
# user header keeps the records isolated from other users.
Invoke-Hermes -Arguments @("config", "set", "model.context_length", [string]$ContextWindow)
Invoke-Hermes -Arguments @("config", "set", "providers.watson-llama.extra_headers.x-headroom-project-id", $ProjectId)
Invoke-Hermes -Arguments @("config", "set", "providers.watson-llama.extra_headers.x-headroom-user-id", $UserId)
Invoke-Hermes -Arguments @("config", "set", "headroom.memory_scope", "global")
Invoke-Hermes -Arguments @("config", "check")

if (-not $NoRestart) {
    Invoke-Hermes -Arguments @("gateway", "restart")
    & wsl.exe -d Ubuntu -- timeout 15 systemctl --user is-active hermes-gateway
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes gateway did not become active after restart."
    }
}

Write-Host "Hermes runtime metadata synchronized." -ForegroundColor Green
Write-Host "  Context window : $ContextWindow"
Write-Host "  Memory project : $ProjectId"
Write-Host "  Memory user    : $UserId"
