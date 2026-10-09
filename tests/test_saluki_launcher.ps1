$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $repo 'scripts\windows\Start-SalukiStack.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }

foreach ($name in @('Get-CommandLineOptionValue', 'Get-SalukiServerArguments', 'Test-SalukiRuntime', 'Test-ResourceReserve', 'Wait-ResourceReserve')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Saluki launcher helper '$name' is missing." }
    . ([scriptblock]::Create($node.Extent.Text))
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
if (-not (Test-ResourceReserve -FreeVramMiB 2048 -AvailableRamBytes (2GB) -MinimumFreeVramMiB 2048)) {
    throw 'The exact minimum resource reserve should pass.'
}
if (Test-ResourceReserve -FreeVramMiB 2047 -AvailableRamBytes (2GB) -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB free VRAM.'
}
if (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (2GB - 1) -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB available host RAM.'
}

function Get-FreeVramMiB { return 4096 }
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
$reserve = Wait-ResourceReserve -MinimumFreeVramMiB 2048 -RequiredHealthySamples 2
if ($reserve.AvailableRamBytes -ne 3GB) { throw 'The reserve wait did not return the stable healthy sample.' }

Write-Host 'PASS: Saluki launch profile pins the tested model/runtime, requires projector and GPU offload, and cannot route Qwen.' -ForegroundColor Green
