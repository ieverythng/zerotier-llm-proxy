param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "..\..\config\server\litellm-config.yaml"),
    [string]$LlamaCppBaseUrl = "http://127.0.0.1:8080/v1",
    [string]$ListenHost = "0.0.0.0",
    [int]$ListenPort = 4000,
    [string]$BackendKey = "local-qwen36",
    [string]$UpstreamModel = "qwen3.8",
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ModelName = "qwen3.8",
    [string]$LiteLLMExecutable = '',
    [switch]$UseStaticConfig
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Resolve-LiteLLMExecutable {
    param(
        [string]$RequestedPath,
        [string]$RepositoryRoot,
        [bool]$MasterKeyAvailable
    )

    if ($RequestedPath) {
        if (-not (Test-Path -LiteralPath $RequestedPath -PathType Leaf)) {
            throw "Requested LiteLLM executable does not exist: $RequestedPath"
        }
        $resolvedRequest = (Resolve-Path -LiteralPath $RequestedPath).Path
        $projectPath = [IO.Path]::GetFullPath((Join-Path $RepositoryRoot '.runtime\litellm\.venv\Scripts\litellm.exe'))
        if ([IO.Path]::GetFullPath($resolvedRequest) -eq $projectPath -and -not $MasterKeyAvailable) {
            throw 'The project LiteLLM 1.104.2 proxy requires LITELLM_MASTER_KEY; refusing to start it without authentication.'
        }
        return $resolvedRequest
    }

    $command = Get-Command litellm -CommandType Application -ErrorAction Stop
    return $command.Source
}

function Test-HttpJson {
    param(
        [string]$Uri,
        [string]$ApiKey = ""
    )

    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
        $headers.Authorization = "Bearer $ApiKey"
    }

    try {
        return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get -TimeoutSec 10
    } catch {
        throw "HTTP check failed for $Uri. $($_.Exception.Message)"
    }
}

function Get-LegacyQwenAliasYaml {
    param(
        [string]$ModelName,
        [string]$UpstreamModel
    )

    if ($ModelName -ne 'qwen3.8' -or $UpstreamModel -ne 'qwen3.8') {
        return ''
    }

    return @'
  - model_name: qwen36-turbo-hermes
    litellm_params:
      model: openai/qwen3.8
      api_base: __API_BASE__
      api_key: os.environ/LLAMA_CPP_API_KEY
  - model_name: qwen36-turbo-hermes-llama
    litellm_params:
      model: openai/qwen3.8
      api_base: __API_BASE__
      api_key: os.environ/LLAMA_CPP_API_KEY
  - model_name: qwen36-turbo-hermes-spec
    litellm_params:
      model: openai/qwen3.8
      api_base: __API_BASE__
      api_key: os.environ/LLAMA_CPP_API_KEY
'@
}

$litellmMasterKey = $env:LITELLM_MASTER_KEY
if ([string]::IsNullOrWhiteSpace($litellmMasterKey)) {
    $litellmMasterKey = [Environment]::GetEnvironmentVariable('LITELLM_MASTER_KEY', 'User')
    if (-not [string]::IsNullOrWhiteSpace($litellmMasterKey)) { $env:LITELLM_MASTER_KEY = $litellmMasterKey }
}
$litellmExe = Resolve-LiteLLMExecutable -RequestedPath $LiteLLMExecutable -RepositoryRoot $repoRoot -MasterKeyAvailable (-not [string]::IsNullOrWhiteSpace($litellmMasterKey))

$models = Test-HttpJson -Uri "$LlamaCppBaseUrl/models" -ApiKey $BackendKey
$availableModels = @()
if ($models.data) { $availableModels += @($models.data | ForEach-Object { $_.id }) }
if ($models.models) { $availableModels += @($models.models | ForEach-Object { $_.id; $_.name; $_.model }) }

if (-not ($availableModels -contains $UpstreamModel)) {
    throw "llama.cpp is reachable, but $UpstreamModel was not listed by $LlamaCppBaseUrl/models."
}

if ($UseStaticConfig) {
    $resolvedConfig = Resolve-Path -Path $ConfigPath
} else {
    $runtimeDir = Join-Path $PSScriptRoot "..\..\_tmp\litellm"
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $runtimeConfig = Join-Path $runtimeDir "llama-cpp-active.yaml"
    $legacyQwenAliases = Get-LegacyQwenAliasYaml -ModelName $ModelName -UpstreamModel $UpstreamModel
    if ($legacyQwenAliases) {
        $legacyQwenAliases = $legacyQwenAliases.Replace('__API_BASE__', $LlamaCppBaseUrl)
    }
    @"
model_list:
  - model_name: $ModelName
    litellm_params:
      model: openai/$UpstreamModel
      api_base: $LlamaCppBaseUrl
      api_key: os.environ/LLAMA_CPP_API_KEY
$legacyQwenAliases
litellm_settings:
  drop_params: true
  request_timeout: 600
  set_verbose: false
  stream_options_include_usage: true
"@ | Set-Content -LiteralPath $runtimeConfig -Encoding ASCII
    $resolvedConfig = Resolve-Path -Path $runtimeConfig
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

Write-Host "llama.cpp model check passed: $UpstreamModel"
Write-Host "Starting LiteLLM on http://$ListenHost`:$ListenPort using $resolvedConfig and $litellmExe"

& $litellmExe --config $resolvedConfig --host $ListenHost --port $ListenPort
