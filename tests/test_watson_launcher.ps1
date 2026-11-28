$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $repo 'scripts\windows\Start-WatsonStack.ps1'
$launcherSource = Get-Content -LiteralPath $launcher -Raw
$runtimePreflightIndex = $launcherSource.IndexOf('-ValidateRuntimeOnly')
$llamaPhaseIndex = $launcherSource.IndexOf('Phase 1: llama.cpp')
if ($runtimePreflightIndex -lt 0 -or $runtimePreflightIndex -gt $llamaPhaseIndex) {
    throw 'Watson must validate the authenticated repo-local LiteLLM runtime before starting llama.cpp.'
}
if ($launcherSource -notmatch '(?s)"-LiteLLMExecutable",\s*\$litellmExecutable') {
    throw 'Watson must pass its verified repo-local LiteLLM executable explicitly; PATH fallback is forbidden.'
}
if ($launcherSource -notmatch 'Test-JsonEndpoint -Uri "\$litellmBaseUrl/models" -ApiKey \$litellmMasterKey') {
    throw 'Watson must authenticate its LiteLLM readiness checks with the configured master key.'
}
$requiredPreflightTokens = @(
    '$probe.Refresh()',
    '$null -ne $exitCode',
    'IsNullOrWhiteSpace($probeOutput)'
)
foreach ($token in $requiredPreflightTokens) {
    if ($launcherSource -notlike "*$token*") {
        throw "NVIDIA preflight regression guard is missing: $token"
    }
}
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }
$stop = $ast.Find({ param($n)
    $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -in @('Stop-Process', 'taskkill.exe')
}, $true)
if ($stop -or $launcherSource.Contains('stop_llama_server.ps1')) {
    throw 'The launcher must never terminate an occupied-port owner, even after an authentication or model mismatch.'
}

# Load the validation functions only; use fixtures in place of network/process observations.
foreach ($name in @('Get-CommandLineOptionValue', 'Test-RequestedLlamaRuntime', 'Test-JsonEndpoint', 'Get-SingleListenerProcess', 'Test-LiteLLMListener', 'Confirm-ExistingLiteLLM')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Missing offline test seam: $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}

$script:capturedHeaders = $null
$script:fixtureHttpError = $null
function Invoke-RestMethod {
    param([string]$Uri, [hashtable]$Headers, [string]$Method, [int]$TimeoutSec, [int]$MaximumRedirection)
    if (-not $PSBoundParameters.ContainsKey('MaximumRedirection') -or $MaximumRedirection -ne 0) {
        throw 'Readiness probes must not follow redirects.'
    }
    $script:capturedHeaders = $Headers
    if ($script:fixtureHttpError) { throw $script:fixtureHttpError }
    return @{ ok = $true }
}
if (-not (Test-JsonEndpoint -Uri 'http://llama.test/v1/models' -ApiKey 'llama.cpp')) {
    throw 'Authenticated model endpoint probe did not return its response.'
}
if ($script:capturedHeaders.Authorization -ne 'Bearer llama.cpp') {
    throw 'Model endpoint probe did not attach the configured Bearer API key.'
}
if (-not (Test-JsonEndpoint -Uri 'http://proxy.test/v1/models')) {
    throw 'Unauthenticated proxy endpoint probe did not return its response.'
}
if ($script:capturedHeaders.Count -ne 0) {
    throw 'The generic proxy health probe unexpectedly received an API key.'
}
if (-not (Test-JsonEndpoint -Uri 'http://proxy.test/v1/models' -ApiKey 'litellm-test-key')) {
    throw 'Authenticated LiteLLM endpoint probe did not return its response.'
}
if ($script:capturedHeaders.Authorization -ne 'Bearer litellm-test-key') {
    throw 'LiteLLM readiness probe did not attach its Bearer master key.'
}
$script:fixtureHttpError = 'Synthetic HTTP 401'
if ($null -ne (Test-JsonEndpoint -Uri 'http://proxy.test/v1/models' -ApiKey 'litellm-test-key')) {
    throw 'An authentication rejection must not count as readiness.'
}

$liteFixture = 'C:\synthetic\litellm.exe'
$created = [datetime]'2026-01-01T00:00:00'
$script:fixtureListeners = @([pscustomobject]@{ LocalPort = 4000; OwningProcess = 500 })
$script:fixtureProcesses = @{
    '500' = [pscustomobject]@{ ExecutablePath = 'C:\synthetic\python.exe'; ParentProcessId = 400; CreationDate = $created.AddSeconds(1) }
    '400' = [pscustomobject]@{ ExecutablePath = $liteFixture; CreationDate = $created }
}
function Get-NetTCPConnection { param($State, $ErrorAction); $script:fixtureListeners }
function Get-CimInstance { param($Class, $Filter, $ErrorAction); $script:fixtureProcesses[($Filter -replace '^ProcessId = ')] }
if (-not (Test-LiteLLMListener -Port 4000 -ExpectedExecutable $liteFixture)) {
    throw 'A Python listener launched by the repo-local LiteLLM executable must be recognized.'
}
$script:fixtureProcesses['400'].CreationDate = $created.AddSeconds(2)
if (Test-LiteLLMListener -Port 4000 -ExpectedExecutable $liteFixture) {
    throw 'A newer process with a reused parent PID cannot attest the listener.'
}
$script:fixtureProcesses['400'].CreationDate = $created
$listenerArgs = @{ Port = 4000; ExpectedExecutable = $liteFixture; BaseUrl = 'http://proxy.test/v1'; ApiKey = 'synthetic-key'; ModelName = 'synthetic-model' }
$failure = try { Confirm-ExistingLiteLLM @listenerArgs } catch { $_.Exception.Message }
if ($failure -notlike '*authenticated model check*no process was stopped*') {
    throw 'An identified LiteLLM listener that rejects the key must refuse reattachment.'
}
$script:capturedHeaders = $null
$failure = try { Confirm-ExistingLiteLLM @listenerArgs -Replace } catch { $_.Exception.Message }
if ($failure -notlike '*cannot stop an existing listener*' -or $null -ne $script:capturedHeaders) {
    throw 'Legacy replacement must refuse before probing an occupied port.'
}
$script:fixtureListeners += [pscustomobject]@{ LocalPort = 4000; OwningProcess = 501 }
$failure = try { Confirm-ExistingLiteLLM @listenerArgs } catch { $_.Exception.Message }
if ($failure -notlike '*unverified listener*' -or $null -ne $script:capturedHeaders) {
    throw 'An additional unknown owner must cause refusal before any credential is sent.'
}
if ($launcherSource -notmatch 'Test-WatsonChatHealth\.ps1.*-ApiKey \$BackendKey') {
    throw 'The bounded chat health check does not pass the backend API key.'
}
$official = 'C:\Users\Admin\PROJECTS\llama-b10621-win-cuda133'
$model = 'D:\MODELS\Qwen3.8-27B-UD-IQ3_S.gguf'
$arguments = " -m $model -b 512 -ub 256"
$expected = @{
    RequestedModelPath = $model
    RequestedBinDir = $official
    RequestedBatchSize = 512
    RequestedUBatchSize = 256
    RequestedSkipChatParsing = $false
}
$good = '"' + $official + '\llama-server.exe"' + $arguments
$bad = '"C:\Users\Admin\PROJECTS\llama-cpp-server\_tmp\llama-bin-wslbridge\llama-server.exe"' + $arguments
if (-not (Test-RequestedLlamaRuntime -CommandLine $good @expected)) { throw 'Expected official runtime to match.' }
if (Test-RequestedLlamaRuntime -CommandLine $bad @expected) { throw 'Old binary was incorrectly accepted.' }
if (Test-RequestedLlamaRuntime -CommandLine ($good + ' --skip-chat-parsing') @expected) { throw 'Parser mismatch was accepted.' }
if (Test-RequestedLlamaRuntime -CommandLine ($good.Replace('-b 512', '-b 1024')) @expected) { throw 'Batch mismatch was accepted.' }

foreach ($file in @($launcher, (Join-Path $repo 'scripts\windows\Start-Qwen36ZeroTierStack.ps1'))) {
    $scriptAst = [Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
    $defaultScript = $scriptAst.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'LlamaScript' }
    if ($defaultScript.DefaultValue.Value -ne 'scripts\start_profile.ps1') { throw "Unsafe default launcher in $file" }
}
'PASS: official binary identity, parser/batch matching, and both entry-point defaults.'
