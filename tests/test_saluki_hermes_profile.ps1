$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $repo 'scripts\windows\Set-HermesSalukiProfile.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'Saluki Hermes profile script is missing.' }

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }
$node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-SalukiHermesProfileSettings' }, $true)
if (-not $node) { throw 'Saluki Hermes profile settings builder is missing.' }
. ([scriptblock]::Create($node.Extent.Text))
$matchNode = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-SalukiHermesProfileMatch' }, $true)
if (-not $matchNode) { throw 'Saluki Hermes profile idempotency check is missing.' }
. ([scriptblock]::Create($matchNode.Extent.Text))

$settings = @(Get-SalukiHermesProfileSettings -ContextWindow 150000)
$actual = @{}
foreach ($setting in $settings) { $actual[$setting.Key] = $setting.Value }
foreach ($expected in @{
    'model.default' = 'saluki27b'
    'model.context_length' = '150000'
    'model.provider' = 'watson-llama'
    'providers.watson-llama.default_model' = 'saluki27b'
    'providers.watson-llama.models.saluki27b.context_length' = '150000'
    'providers.watson-llama.models.saluki27b.supports_vision' = 'true'
}.GetEnumerator()) {
    if ($actual[$expected.Key] -ne $expected.Value) {
        throw "Saluki Hermes setting '$($expected.Key)' should be '$($expected.Value)' (got '$($actual[$expected.Key])')."
    }
}
if ($actual.Keys -match 'extra_headers|base_url|api$') {
    throw 'Saluki profile switching must preserve the active Headroom URL and memory/auth headers.'
}
if (-not (Test-SalukiHermesProfileMatch -DefaultModel 'saluki27b' -ModelContext 150000 -ProviderDefaultModel 'saluki27b' -CatalogContext 150000 -SupportsVision $true -ContextWindow 150000)) {
    throw 'The Saluki profile matcher rejected an already synchronized profile.'
}
if (Test-SalukiHermesProfileMatch -DefaultModel 'saluki27b' -ModelContext 100096 -ProviderDefaultModel 'saluki27b' -CatalogContext 100096 -SupportsVision $true -ContextWindow 150000) {
    throw 'The Saluki profile matcher accepted stale context metadata.'
}

if (Test-SalukiHermesProfileMatch -DefaultModel 'saluki27b' -Provider 'other-provider' -ModelContext 150000 -ProviderDefaultModel 'saluki27b' -CatalogContext 150000 -SupportsVision $true -ContextWindow 150000) {
    throw 'The Saluki profile matcher accepted the wrong active provider.'
}
foreach ($helper in @('Get-SalukiCompressionProfile', 'Test-SalukiCompressionProfileMatch')) {
    $helperNode = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $helper }, $true)
    if (-not $helperNode) { throw "Missing Saluki compression helper: $helper" }
    . ([scriptblock]::Create($helperNode.Extent.Text))
}
$currentCompression = [pscustomobject]@{
    provider = 'custom'; model = 'qwen3.8'; base_url = 'http://172.24.16.1:4000/v1'; timeout = 321
    fallback_chain = @(
        [pscustomobject]@{ provider = 'custom'; model = 'qwen3.8'; base_url = 'http://172.24.16.1:4000/v1'; timeout = 123; api_mode = 'chat_completions'; extra_headers = @{ 'x-test' = 'preserved' } },
        [pscustomobject]@{ provider = 'hosted'; model = 'hosted-summary'; timeout = 42 }
    )
}
$compression = Get-SalukiCompressionProfile -Compression $currentCompression -HeadroomBaseUrl 'http://172.24.16.1:8787/v1'
if ($compression.model -ne 'saluki27b' -or $compression.provider -ne 'custom' -or $compression.base_url -ne $currentCompression.base_url) {
    throw 'The primary compressor did not follow Saluki while preserving its direct route.'
}
if ($compression.fallback_chain.Count -ne 2 -or $compression.fallback_chain[0].model -ne 'saluki27b' -or $compression.fallback_chain[0].timeout -ne 123 -or $compression.fallback_chain[0].extra_headers['x-test'] -ne 'preserved') {
    throw 'The local compression fallback lost sibling settings or retained the old model.'
}
if ($compression.fallback_chain[1].model -ne 'hosted-summary' -or $currentCompression.fallback_chain[0].model -ne 'qwen3.8') {
    throw 'Compression planning mutated an independent fallback or its input snapshot.'
}
if (Test-SalukiCompressionProfileMatch -Current $currentCompression -Desired $compression) { throw 'Stale compressor identity was treated as synchronized.' }
if (-not (Test-SalukiCompressionProfileMatch -Current $compression -Desired (Get-SalukiCompressionProfile -Compression $compression -HeadroomBaseUrl 'http://172.24.16.1:8787/v1'))) { throw 'Compression planning is not idempotent.' }
$initialCompression = Get-SalukiCompressionProfile -Compression ([pscustomobject]@{}) -HeadroomBaseUrl 'http://172.24.16.1:8787/v1'
if ($initialCompression.base_url -ne 'http://172.24.16.1:4000/v1' -or $initialCompression.fallback_chain[0].model -ne 'saluki27b') { throw 'Initial compression route did not use direct LiteLLM.' }
$rejectedBadRoute = $false
try { Get-SalukiCompressionProfile -Compression ([pscustomobject]@{}) -HeadroomBaseUrl 'not-a-route' | Out-Null } catch { $rejectedBadRoute = $true }
if (-not $rejectedBadRoute) { throw 'Compression planning invented a fallback for an invalid route.' }
$source = Get-Content -LiteralPath $scriptPath -Raw
foreach ($required in @('-Provider $profileState.provider', 'auxiliary.compression.fallback_chain', 'Invoke-HermesJsonSetting')) {
    if (-not $source.Contains($required)) { throw "Live Saluki synchronization is missing $required" }
}

'PASS: Saluki profile sync checks provider, synchronizes local compression identity and preserves routing/header siblings.'
