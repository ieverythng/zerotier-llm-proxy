param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "..\..\config\server\litellm-config.yaml"),
    [string]$LlamaCppBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$ListenHost = "0.0.0.0",
    [int]$ListenPort = 4000,
    [string]$BackendKey = "llama.cpp"
)

$ErrorActionPreference = "Stop"

function Test-HttpJson {
    param([string]$Uri)

    try {
        return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 10
    } catch {
        throw "HTTP check failed for $Uri. $($_.Exception.Message)"
    }
}

$resolvedConfig = Resolve-Path -Path $ConfigPath
$models = Test-HttpJson -Uri "$LlamaCppBaseUrl/models"

if (-not (($models.data | ForEach-Object { $_.id }) -contains "qwen36-turbo-hermes")) {
    throw "llama.cpp is reachable, but qwen36-turbo-hermes was not listed by $LlamaCppBaseUrl/models."
}

$ztAddresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -like "*ZeroTier*" -or $_.IPAddress -like "10.88.*" } |
    Select-Object -ExpandProperty IPAddress

if ($ztAddresses) {
    Write-Host "ZeroTier candidate address(es): $($ztAddresses -join ', ')"
} else {
    Write-Warning "No ZeroTier IPv4 address detected from PowerShell. The proxy will still bind to $ListenHost."
}

$env:LLAMA_CPP_API_KEY = $BackendKey
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "llama.cpp model check passed: qwen36-turbo-hermes"
Write-Host "Starting LiteLLM on http://$ListenHost`:$ListenPort using $resolvedConfig"

& litellm --config $resolvedConfig --host $ListenHost --port $ListenPort
