$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $repo 'scripts\windows\Start-WatsonStack.ps1'
$launcherSource = Get-Content -LiteralPath $launcher -Raw
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

# Load pure validation functions without starting any services.
foreach ($name in @('Get-CommandLineOptionValue', 'Test-RequestedLlamaRuntime', 'Test-JsonEndpoint')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    . ([scriptblock]::Create($node.Extent.Text))
}

$script:capturedHeaders = $null
function Invoke-RestMethod {
    param([string]$Uri, [hashtable]$Headers, [string]$Method, [int]$TimeoutSec)
    $script:capturedHeaders = $Headers
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
