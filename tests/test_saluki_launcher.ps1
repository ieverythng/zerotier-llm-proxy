$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $repo 'scripts\windows\Start-SalukiStack.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }
$launcherSource = Get-Content -LiteralPath $launcher -Raw
$securityPreflightIndex = $launcherSource.IndexOf('-ValidateRuntimeOnly')
$modelLaunchIndex = $launcherSource.IndexOf('Start-Process -FilePath $llamaExe')
if ($securityPreflightIndex -lt 0 -or $securityPreflightIndex -gt $modelLaunchIndex) {
    throw 'Saluki must validate the authenticated repo-local LiteLLM runtime before allocating model VRAM/RAM.'
}
if ($launcherSource -notmatch 'Set-HermesSalukiProfile\.ps1' -or $launcherSource -notmatch 'ContextWindow \$ContextSize') {
    throw 'Saluki launcher must sync Hermes to the live model context after the serving stack is healthy.'
}

foreach ($name in @('Get-CommandLineOptionValue', 'Get-NormalizedContextSize', 'Get-SalukiServerArguments', 'Test-SalukiRuntime', 'ConvertFrom-NvidiaSmiMetric', 'Get-GpuUtilizationPercent', 'Test-ResourceReserve', 'Wait-ResourceReserve', 'Invoke-FinalResourceGate')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Saluki launcher helper '$name' is missing." }
    . ([scriptblock]::Create($node.Extent.Text))
}

if ((ConvertFrom-NvidiaSmiMetric -Output @('0') -MetricName 'GPU utilization') -ne 0) {
    throw 'The GPU metric parser did not accept a valid zero-utilization sample.'
}
if ((ConvertFrom-NvidiaSmiMetric -Output @(' 2048 ') -MetricName 'free VRAM') -ne 2048) {
    throw 'The GPU metric parser did not trim a valid free-VRAM sample.'
}
$invalidGpuMetricRejected = $false
try { [void](ConvertFrom-NvidiaSmiMetric -Output @('N/A') -MetricName 'GPU utilization') } catch { $invalidGpuMetricRejected = $true }
if (-not $invalidGpuMetricRejected) { throw 'The GPU metric parser accepted an unavailable sample.' }

if ((Get-NormalizedContextSize -ContextSize 100000) -ne 100096) {
    throw 'The 100k Saluki request was not aligned to llama.cpp context granularity.'
}
if ((Get-NormalizedContextSize -ContextSize 150000) -ne 150016) {
    throw 'The 150k Saluki challenger was not aligned to llama.cpp context granularity.'
}
if ((Get-NormalizedContextSize -ContextSize 262144) -ne 262144) {
    throw 'An already-aligned maximum Saluki context should remain unchanged.'
}

$model = 'D:\MODELS\Underdog-Saluki-27B-1.0-IQ2-mix.gguf'
$mmproj = 'D:\MODELS\mmproj-Underdog-Saluki-27B-1.0-F16.gguf'
$exe = 'C:\Users\Admin\PROJECTS\llama-b11433-win-cuda134\llama\llama-server.exe'
$args = Get-SalukiServerArguments -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp'
$commandLine = '"' + $exe + '" ' + ($args -join ' ')

if (-not (Test-SalukiRuntime -CommandLine $commandLine -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp')) {
    throw 'Known-good Saluki command line was not recognized.'
}

$withoutProjectorOffload = $commandLine -replace '--mmproj-offload\s*', ''
if (Test-SalukiRuntime -CommandLine $withoutProjectorOffload -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp') {
    throw 'Runtime matcher accepted a server without projector GPU offload.'
}

if (($args -join ' ') -match '(?i)qwen') { throw 'Saluki launcher arguments unexpectedly mention Qwen.' }
if (($args -join ' ') -notmatch '--mmproj-offload' -or ($args -join ' ') -notmatch '-ngl 99') {
    throw 'Saluki launcher does not request all-layer and projector GPU offload.'
}
if (($args -join ' ') -notmatch '--image-min-tokens 1024') {
    throw 'Saluki vision launcher does not use the model card minimum image-token floor.'
}
foreach ($required in @('-ctk q8_0', '-ctv q8_0', '-cram 0')) {
    if (($args -join ' ') -notmatch [regex]::Escape($required)) {
        throw "The default Saluki profile is missing '$required'."
    }
}

$extendedArgs = Get-SalukiServerArguments -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150000 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0'
$extendedCommandLine = '"' + $exe + '" ' + ($extendedArgs -join ' ')
if (-not (Test-SalukiRuntime -CommandLine $extendedCommandLine -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150000 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0')) {
    throw 'The Saluki runtime matcher rejected a 150k Q4 KV context challenger.'
}
foreach ($required in @('-c 150000', '-ub 128', '-ctk q4_0', '-ctv q4_0')) {
    if (($extendedArgs -join ' ') -notmatch [regex]::Escape($required)) {
        throw "The 150k Saluki challenger is missing '$required'."
    }
}

$wrongCache = $extendedCommandLine -replace '(?i)-ctv q4_0', '-ctv q8_0'
if (Test-SalukiRuntime -CommandLine $wrongCache -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150000 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0') {
    throw 'The Saluki runtime matcher accepted a mismatched V-cache quantization.'
}

$partialArgs = Get-SalukiServerArguments -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150016 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0' -GpuLayerCount 56
$partialCommand = '"' + $exe + '" ' + ($partialArgs -join ' ')
if (-not (Test-SalukiRuntime -CommandLine $partialCommand -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150016 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0' -GpuLayerCount 56)) {
    throw 'The Saluki runtime matcher rejected the 56-layer Sunshine-headroom challenger.'
}
if (($partialArgs -join ' ') -notmatch '-ngl 56') {
    throw 'The partial GPU offload challenger did not request exactly 56 GPU layers.'
}
$wrongGpuLayers = $partialCommand -replace '(?i)-ngl 56', '-ngl 99'
if (Test-SalukiRuntime -CommandLine $wrongGpuLayers -ExpectedExecutable $exe -ModelPath $model -MmprojPath $mmproj -Port 8080 -ContextSize 150016 -BatchSize 512 -UBatchSize 128 -ApiKey 'llama.cpp' -KVCacheType 'q4_0' -GpuLayerCount 56) {
    throw 'The Saluki runtime matcher accepted the wrong number of GPU-offloaded layers.'
}

if (-not (Test-ResourceReserve -FreeVramMiB 2048 -AvailableRamBytes (2GB) -MinimumFreeVramMiB 2048)) {
    throw 'The exact minimum resource reserve should pass.'
}
if (Test-ResourceReserve -FreeVramMiB 2047 -AvailableRamBytes (2GB) -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB free VRAM.'
}
if (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (2GB - 1) -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB available host RAM.'
}
if (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (2.99GB) -MinimumFreeVramMiB 2048 -MinimumAvailableRamGiB 3) {
    throw 'The resource gate ignored the configured cold-start RAM reserve.'
}
if (-not (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (4GB) -MinimumFreeVramMiB 2048 -GpuUtilizationPct 92 -MaximumGpuUtilizationPct 92)) {
    throw 'The resource gate rejected the exact 8% GPU headroom limit.'
}
if (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (4GB) -MinimumFreeVramMiB 2048 -GpuUtilizationPct 93 -MaximumGpuUtilizationPct 92) {
    throw 'The resource gate accepted less than 8% GPU headroom.'
}

function Get-FreeVramMiB { return 4096 }
function Get-GpuUtilizationPercent { return 0 }
$script:fakeClock = [datetime]'2026-10-09T00:00:00'
$script:fakeRamSample = 0
function Get-AvailableRamBytes {
    $sample = $script:fakeRamSample
    $script:fakeRamSample++
    if ($sample -lt 6) { return 1GB }
    return 3GB
}
function Get-Date { return $script:fakeClock }
function Start-Sleep {
    param([int]$Seconds)
    $script:fakeClock = $script:fakeClock.AddSeconds($Seconds)
}
$reserve = Wait-ResourceReserve -MinimumFreeVramMiB 2048 -RequiredHealthySamples 2 -MinimumAvailableRamGiB 3
if ($reserve.AvailableRamBytes -ne 3GB) { throw 'The reserve wait did not return the stable healthy sample.' }

function Wait-ResourceReserve { throw 'simulated final reserve failure' }
function Stop-SpawnedServer { param([int]$ProcessId); $script:stoppedPid = $ProcessId }
$script:stoppedPid = 0
try { [void](Invoke-FinalResourceGate -MinimumFreeVramMiB 2048 -MaximumGpuUtilizationPercent 92 -ProcessId 12345) } catch { }
if ($script:stoppedPid -ne 12345) {
    throw 'A final reserve failure left a newly spawned Saluki process running.'
}

Write-Host 'PASS: Saluki launch profile pins the tested model/runtime, requires projector and GPU offload, and cannot route Qwen.' -ForegroundColor Green
