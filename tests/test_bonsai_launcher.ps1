$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $repo 'scripts\windows\Start-BonsaiStack.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }

foreach ($name in @('Get-CommandLineOptionValue', 'Get-BonsaiServerArguments', 'Test-BonsaiRuntime', 'Test-ResourceReserve', 'Get-BonsaiStackArguments', 'Test-ReplaceableLlamaProcess')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Bonsai launcher helper '$name' is missing." }
    . ([scriptblock]::Create($node.Extent.Text))
}

$model = 'D:\MODELS\Ternary-Bonsai-27B-Q2_0.gguf'
$exe = 'C:\Users\Admin\PROJECTS\llama-cpp-prism-bonsai\build-cuda-bonsai\bin\Release\llama-server.exe'
$serverArgs = Get-BonsaiServerArguments -ModelPath $model -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp'
$commandLine = '"' + $exe + '" ' + ($serverArgs -join ' ')

if (-not (Test-BonsaiRuntime -CommandLine $commandLine -ExpectedExecutable $exe -ModelPath $model -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp')) {
    throw 'Known-good Bonsai command line was not recognized.'
}

$wrongCache = $commandLine -replace '(?i)-ctk q8_0', '-ctk f16'
if (Test-BonsaiRuntime -CommandLine $wrongCache -ExpectedExecutable $exe -ModelPath $model -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp') {
    throw 'Runtime matcher accepted a Bonsai server with a different K-cache type.'
}

$withoutDirectIo = $commandLine -replace '(?i)(^|\s)-dio(\s|$)', '$1$2'
if (Test-BonsaiRuntime -CommandLine $withoutDirectIo -ExpectedExecutable $exe -ModelPath $model -Port 8080 -ContextSize 100096 -BatchSize 512 -UBatchSize 256 -ApiKey 'llama.cpp') {
    throw 'Runtime matcher accepted a server without the tested direct-I/O setting.'
}

if (($serverArgs -join ' ') -match '(?i)qwen') { throw 'Bonsai launcher arguments unexpectedly mention another model.' }
foreach ($required in @('-ngl 99', '-ctk q8_0', '-ctv q8_0', '-dio', '--reasoning off', '-np 1')) {
    if (($serverArgs -join ' ') -notmatch [regex]::Escape($required)) { throw "Bonsai server arguments are missing '$required'." }
}

$projects = 'C:\Users\Admin\PROJECTS'
if (-not (Test-ReplaceableLlamaProcess -ProcessName 'llama-server' -ExecutablePath "$projects\llama-cpp-prism-bonsai\build\llama-server.exe" -CommandLine '"C:\Users\Admin\PROJECTS\llama-cpp-prism-bonsai\build\llama-server.exe" --port 8080' -Port 8080 -ProjectsRoot $projects)) {
    throw 'Explicit replacement should accept a local PROJECTS llama-server on the expected port.'
}
if (Test-ReplaceableLlamaProcess -ProcessName 'llama-server' -ExecutablePath 'C:\Program Files\llama-server.exe' -CommandLine '"C:\Program Files\llama-server.exe" --port 8080' -Port 8080 -ProjectsRoot $projects) {
    throw 'Explicit replacement must reject llama-server processes outside PROJECTS.'
}
if (Test-ReplaceableLlamaProcess -ProcessName 'other-service' -ExecutablePath "$projects\llama-server.exe" -CommandLine '"C:\Users\Admin\PROJECTS\llama-server.exe" --port 8080' -Port 8080 -ProjectsRoot $projects) {
    throw 'Explicit replacement must reject non-llama-server processes.'
}
if (Test-ReplaceableLlamaProcess -ProcessName 'llama-server' -ExecutablePath "$projects\llama-server.exe" -CommandLine '"C:\Users\Admin\PROJECTS\llama-server.exe" --port 9090' -Port 8080 -ProjectsRoot $projects) {
    throw 'Explicit replacement must reject a process with a different port argument.'
}

$stackArgs = Get-BonsaiStackArguments -StackScript 'C:\repo\Start-WatsonStack.ps1' -ModelPath $model -LlamaBinDir (Split-Path $exe) -ContextSize 100096 -BatchSize 512 -UBatchSize 256
if ($stackArgs -notcontains '-SkipLlamaStart' -or $stackArgs -notcontains '-SkipHermesSync' -or $stackArgs -notcontains '-SkipCoherenceCheck') {
    throw 'Bonsai proxy setup must reuse the validated model without reloading or generating during startup.'
}
if ($stackArgs -notcontains '-Model' -or $stackArgs[$stackArgs.IndexOf('-Model') + 1] -ne 'bonsai27b') {
    throw 'Bonsai proxy setup did not pin the canonical Bonsai model name.'
}
if ($stackArgs -notcontains '-ServedAlias' -or $stackArgs[$stackArgs.IndexOf('-ServedAlias') + 1] -ne 'bonsai27b') {
    throw 'Bonsai proxy setup did not pin the served alias.'
}

if (-not (Test-ResourceReserve -FreeVramMiB 2048 -AvailableRamBytes 2GB -MinimumFreeVramMiB 2048)) {
    throw 'The exact minimum resource reserve should pass.'
}
if (Test-ResourceReserve -FreeVramMiB 2047 -AvailableRamBytes 2GB -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB free VRAM.'
}
if (Test-ResourceReserve -FreeVramMiB 4096 -AvailableRamBytes (2GB - 1) -MinimumFreeVramMiB 2048) {
    throw 'The resource gate accepted less than 2 GiB available host RAM.'
}

Write-Host 'PASS: Bonsai launcher pins the compatible runtime, Q8 KV, direct I/O, explicit proxy identity, and 2 GiB reserve.' -ForegroundColor Green
