$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $repo 'scripts\windows\Measure-Qwen36DirectPrefill.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }
$node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-ApiHeaders' }, $true)
if (-not $node) { throw 'Direct prefill harness API-header builder is missing.' }
. ([scriptblock]::Create($node.Extent.Text))
$reserveNode = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-BenchmarkResourceReserve' }, $true)
if (-not $reserveNode) { throw 'Direct prefill harness resource-reserve check is missing.' }
. ([scriptblock]::Create($reserveNode.Extent.Text))
$metricNode = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ConvertFrom-NvidiaSmiMetric' }, $true)
if (-not $metricNode) { throw 'Direct prefill harness GPU metric parser is missing.' }
. ([scriptblock]::Create($metricNode.Extent.Text))
$terminalNode = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-CompletionStreamEventTerminal' }, $true)
if (-not $terminalNode) { throw 'Direct prefill harness stream terminator check is missing.' }
. ([scriptblock]::Create($terminalNode.Extent.Text))

$authenticated = New-ApiHeaders -ApiKey 'llama.cpp'
if ($authenticated.Authorization -ne 'Bearer llama.cpp') {
    throw 'Direct prefill harness did not build the llama.cpp bearer header.'
}
if ((New-ApiHeaders -ApiKey '').Count -ne 0) {
    throw 'Direct prefill harness should keep local unauthenticated endpoint support.'
}
if (-not (Test-BenchmarkResourceReserve -AvailableRamBytes (2GB) -FreeVramMiB 2048 -MinimumFreeVramMiB 2048)) {
    throw 'The direct prefill harness rejected the exact two-GiB resource reserve.'
}
if (Test-BenchmarkResourceReserve -AvailableRamBytes (2GB - 1) -FreeVramMiB 2048 -MinimumFreeVramMiB 2048) {
    throw 'The direct prefill harness accepted less than two GiB available RAM.'
}
if (Test-BenchmarkResourceReserve -AvailableRamBytes (2.5GB) -FreeVramMiB 2048 -MinimumFreeVramMiB 2048 -MinimumAvailableRamGiB 3) {
    throw 'The direct prefill harness ignored its configured available-RAM floor.'
}
if (Test-BenchmarkResourceReserve -AvailableRamBytes (4GB) -FreeVramMiB 2047 -MinimumFreeVramMiB 2048) {
    throw 'The direct prefill harness accepted less than two GiB free VRAM.'
}
if ((ConvertFrom-NvidiaSmiMetric -Output @(' 3332 ')) -ne 3332) {
    throw 'The direct prefill harness did not parse a free-VRAM sample.'
}
if (-not (Test-CompletionStreamEventTerminal -Data '[DONE]')) {
    throw 'The direct prefill harness did not recognize the SSE done sentinel.'
}
if (-not (Test-CompletionStreamEventTerminal -Data '{"stop":true,"content":""}')) {
    throw 'The direct prefill harness did not recognize the native completion stop event.'
}
if (Test-CompletionStreamEventTerminal -Data '{"content":"still generating"}') {
    throw 'The direct prefill harness ended a stream before its stop event.'
}

foreach ($name in @('Assert-BenchmarkStreamMode', 'Assert-CompletionStreamDeadline')) {
    $helper = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $helper) { throw "Missing benchmark safety helper: $name" }
    . ([scriptblock]::Create($helper.Extent.Text))
}
Assert-BenchmarkStreamMode -TokenCount 32768 -Streaming $false
Assert-BenchmarkStreamMode -TokenCount 100000 -Streaming $true
$rejectedLongUnmonitored = $false
try { Assert-BenchmarkStreamMode -TokenCount 32769 -Streaming $false } catch { $rejectedLongUnmonitored = $true }
if (-not $rejectedLongUnmonitored) { throw 'A long actual corpus could bypass the mandatory monitored stream.' }
Assert-CompletionStreamDeadline -ElapsedMilliseconds 999 -TimeoutSeconds 1
$rejectedStalledStream = $false
try { Assert-CompletionStreamDeadline -ElapsedMilliseconds 1000 -TimeoutSeconds 1 } catch { $rejectedStalledStream = $true }
if (-not $rejectedStalledStream) { throw 'The stream deadline did not reject an expired request.' }
$source = Get-Content -LiteralPath $scriptPath -Raw
$actualGuard = $source.IndexOf('Assert-BenchmarkStreamMode -TokenCount $tokenCount')
$tokenization = $source.IndexOf('$tokenCount = Invoke-Tokenize -Text $prompt')
$submission = $source.IndexOf('$completionUri =')
if ($actualGuard -le $tokenization -or $actualGuard -ge $submission) { throw 'The actual-token safety check must run after tokenization and before inference.' }
$streamFunction = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-StreamingCompletion' }, $true).Extent.Text
if (($streamFunction -split 'Assert-CompletionStreamDeadline').Count -lt 3) { throw 'The deadline must be checked during both normal and stalled streaming.' }

'PASS: direct prefill auth, actual-token streaming guard and bounded stream deadlines.'
