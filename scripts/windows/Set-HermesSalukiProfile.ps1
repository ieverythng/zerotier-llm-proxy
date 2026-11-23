[CmdletBinding()]
param(
    [ValidateRange(32768, 262144)]
    [int]$ContextWindow = 100096,
    [string]$FallbackBaseUrl = '',
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$hermes = '/home/juanbeck/.local/bin/hermes'

function Get-SalukiHermesProfileSettings {
    param(
        [ValidateRange(32768, 262144)]
        [int]$ContextWindow
    )

    return @(
        [pscustomobject]@{ Key = 'model.default'; Value = 'saluki27b' },
        [pscustomobject]@{ Key = 'model.context_length'; Value = [string]$ContextWindow },
        [pscustomobject]@{ Key = 'model.provider'; Value = 'watson-llama' },
        [pscustomobject]@{ Key = 'providers.watson-llama.default_model'; Value = 'saluki27b' },
        [pscustomobject]@{ Key = 'providers.watson-llama.models.saluki27b.context_length'; Value = [string]$ContextWindow },
        [pscustomobject]@{ Key = 'providers.watson-llama.models.saluki27b.supports_vision'; Value = 'true' }
    )
}

function Test-SalukiHermesProfileMatch {
    param(
        [string]$DefaultModel,
        [int]$ModelContext,
        [string]$ProviderDefaultModel,
        [int]$CatalogContext,
        [bool]$SupportsVision,
        [int]$ContextWindow,
        [string]$Provider = 'watson-llama'
    )

    return ($Provider -eq 'watson-llama') -and
        ($DefaultModel -eq 'saluki27b') -and
        ($ModelContext -eq $ContextWindow) -and
        ($ProviderDefaultModel -eq 'saluki27b') -and
        ($CatalogContext -eq $ContextWindow) -and
        $SupportsVision
}

function Get-SalukiCompressionProfile {
    param(
        $Compression,
        [string]$HeadroomBaseUrl,
        [string]$FallbackBaseUrl = ''
    )

    # Preserve an existing direct compression route. If it has never been
    # configured, derive the established port-4000 route from the Headroom host.
    $baseUrl = $FallbackBaseUrl
    if ([string]::IsNullOrWhiteSpace($baseUrl)) { $baseUrl = [string]$Compression.base_url }
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
        $route = $null
        if (-not [Uri]::TryCreate($HeadroomBaseUrl, [UriKind]::Absolute, [ref]$route) -or $route.Port -ne 8787) {
            throw 'Supply -FallbackBaseUrl when no existing compression route or standard Headroom route is available.'
        }
        $builder = New-Object UriBuilder($route)
        $builder.Port = 4000
        $baseUrl = $builder.Uri.AbsoluteUri
    }
    $uri = $null
    if (-not [Uri]::TryCreate($baseUrl, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('http', 'https') -or $uri.UserInfo) {
        throw 'The compression fallback must be an absolute HTTP(S) URL without embedded credentials.'
    }
    $baseUrl = $baseUrl.TrimEnd('/')
    $chain = @()
    $localFound = $false
    foreach ($entry in @($Compression.fallback_chain)) {
        if ($null -eq $entry) { continue }
        # Clone all fields so timeout, auth references, headers and independent
        # hosted fallbacks survive. Change only rows for this direct local route.
        $copy = [ordered]@{}
        foreach ($property in $entry.PSObject.Properties) { $copy[$property.Name] = $property.Value }
        if ([string]$entry.provider -eq 'custom' -and ([string]$entry.base_url).TrimEnd('/') -eq $baseUrl) {
            $copy['model'] = 'saluki27b'
            $localFound = $true
        }
        $chain += [pscustomobject]$copy
    }
    if (-not $localFound) {
        $chain = @([pscustomobject]@{ provider = 'custom'; model = 'saluki27b'; base_url = $baseUrl; api_mode = 'chat_completions'; timeout = 300 }) + $chain
    }
    return [pscustomobject]@{ provider = 'custom'; model = 'saluki27b'; base_url = $baseUrl; fallback_chain = @($chain) }
}

function Test-SalukiCompressionProfileMatch {
    param($Current, $Desired)

    if ([string]$Current.provider -ne $Desired.provider -or [string]$Current.model -ne $Desired.model -or
        ([string]$Current.base_url).TrimEnd('/') -ne $Desired.base_url) { return $false }
    return (ConvertTo-Json -InputObject @($Current.fallback_chain) -Depth 20 -Compress) -eq
        (ConvertTo-Json -InputObject @($Desired.fallback_chain) -Depth 20 -Compress)
}

function Invoke-HermesJsonSetting {
    param([string]$Key, [string]$Value)

    # Pass JSON via base64: wsl.exe's native argv quoting otherwise strips quotes.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
    $pythonCode = 'import base64,subprocess,sys; value=base64.b64decode(sys.argv[1]).decode(); subprocess.run(sys.argv[2:]+[value],check=True)'
    & wsl.exe -d Ubuntu -- bash -lc "python3 -c '$pythonCode' $encoded /home/juanbeck/.local/bin/hermes config set --force $Key"
    if ($LASTEXITCODE -ne 0) { throw "Hermes JSON setting failed: $Key (exit $LASTEXITCODE)." }
}

function Invoke-Hermes {
    param([string[]]$Arguments)

    & wsl.exe -d Ubuntu -- $hermes @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Hermes command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

$profileStateCode = @'
import json
from pathlib import Path
from ruamel.yaml import YAML
config = YAML(typ='safe').load(Path('/home/juanbeck/.hermes/config.yaml').read_text(encoding='utf-8')) or {}
model = config.get('model') or {}
provider = (config.get('providers') or {}).get('watson-llama') or {}
entry = (provider.get('models') or {}).get('saluki27b') or {}
print(json.dumps({
    'default_model': model.get('default'), 'provider': model.get('provider'),
    'model_context': int(model.get('context_length') or 0),
    'provider_default': provider.get('default_model'),
    'catalog_context': int(entry.get('context_length') or 0),
    'supports_vision': entry.get('supports_vision') is True,
    'headroom_base_url': model.get('base_url') or provider.get('api') or '',
    'compression': (config.get('auxiliary') or {}).get('compression') or {},
}))
'@
$profileStateText = (& wsl.exe -d Ubuntu -e /home/juanbeck/.hermes/hermes-agent/venv/bin/python3 -c $profileStateCode 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the current Hermes Saluki profile.' }
try { $profileState = ConvertFrom-Json -InputObject $profileStateText -ErrorAction Stop }
catch { throw 'Hermes returned an invalid profile snapshot; no settings were changed.' }
$compression = Get-SalukiCompressionProfile -Compression $profileState.compression -HeadroomBaseUrl $profileState.headroom_base_url -FallbackBaseUrl $FallbackBaseUrl
$mainMatched = Test-SalukiHermesProfileMatch -DefaultModel $profileState.default_model -Provider $profileState.provider `
    -ModelContext $profileState.model_context -ProviderDefaultModel $profileState.provider_default `
    -CatalogContext $profileState.catalog_context -SupportsVision $profileState.supports_vision -ContextWindow $ContextWindow
$compressionMatched = Test-SalukiCompressionProfileMatch -Current $profileState.compression -Desired $compression
$alreadyMatched = $mainMatched -and $compressionMatched

if (-not $alreadyMatched) {
    foreach ($setting in @(Get-SalukiHermesProfileSettings -ContextWindow $ContextWindow)) {
        Invoke-Hermes -Arguments @('config', 'set', $setting.Key, $setting.Value)
    }
    foreach ($key in @('provider', 'model', 'base_url')) {
        Invoke-Hermes -Arguments @('config', 'set', "auxiliary.compression.$key", [string]$compression.$key)
    }
    $chainJson = ConvertTo-Json -InputObject @($compression.fallback_chain) -Depth 20 -Compress
    Invoke-HermesJsonSetting -Key 'auxiliary.compression.fallback_chain' -Value $chainJson
    Invoke-Hermes -Arguments @('config', 'check')
}

if (-not $NoRestart -and -not $alreadyMatched) {
    Invoke-Hermes -Arguments @('gateway', 'restart')
    & wsl.exe -d Ubuntu -- timeout 15 systemctl --user is-active hermes-gateway
    if ($LASTEXITCODE -ne 0) {
        throw 'Hermes gateway did not become active after switching to the Saluki profile.'
    }
}

if ($alreadyMatched) {
    Write-Host "Hermes already selects Saluki at context $ContextWindow with vision enabled; the gateway was not restarted." -ForegroundColor Green
} else {
    Write-Host "Hermes now selects Saluki at context $ContextWindow with vision enabled; existing Headroom URL and headers were left untouched." -ForegroundColor Green
}
