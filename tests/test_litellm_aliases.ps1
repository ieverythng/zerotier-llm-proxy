$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $repo 'scripts\windows\Start-Qwen36LiteLLM.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }

$helper = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-LegacyQwenAliasYaml'
}, $true)
if (-not $helper) { throw 'LiteLLM alias helper is missing.' }
. ([scriptblock]::Create($helper.Extent.Text))

$httpHelper = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-HttpJson'
}, $true)
if (-not $httpHelper) { throw 'LiteLLM upstream health-check helper is missing.' }
. ([scriptblock]::Create($httpHelper.Extent.Text))
$script:capturedHeaders = $null
function Invoke-RestMethod {
    param([string]$Uri, [hashtable]$Headers, [string]$Method, [int]$TimeoutSec)
    $script:capturedHeaders = $Headers
    return @{ data = @(@{ id = 'saluki27b' }) }
}
$health = Test-HttpJson -Uri 'http://llama.test/v1/models' -ApiKey 'llama.cpp'
if ($health.data[0].id -ne 'saluki27b') { throw 'Upstream model health-check response was not returned.' }
if ($script:capturedHeaders.Authorization -ne 'Bearer llama.cpp') {
    throw 'LiteLLM upstream health check did not attach the configured Bearer API key.'
}
$qwenAliases = Get-LegacyQwenAliasYaml -ModelName 'qwen3.8' -UpstreamModel 'qwen3.8'
foreach ($alias in @('qwen36-turbo-hermes', 'qwen36-turbo-hermes-llama', 'qwen36-turbo-hermes-spec')) {
    if ($qwenAliases -notmatch [regex]::Escape($alias)) { throw "Expected compatibility alias $alias for Qwen." }
}

foreach ($candidate in @('bonsai27b', 'saluki27b')) {
    $aliases = Get-LegacyQwenAliasYaml -ModelName $candidate -UpstreamModel $candidate
    if (-not [string]::IsNullOrEmpty($aliases)) { throw "Qwen compatibility aliases leaked into candidate model '$candidate'." }
}

Write-Host 'PASS: legacy Qwen names are emitted only when the served and upstream models are Qwen3.8.' -ForegroundColor Green
