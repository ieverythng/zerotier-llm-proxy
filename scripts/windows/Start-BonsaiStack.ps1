[CmdletBinding()]
param(
    [ValidateRange(32768, 131072)]
    [int]$ContextSize = 100096,
    [ValidateRange(128, 2048)]
    [int]$BatchSize = 512,
    [ValidateRange(64, 1024)]
    [int]$UBatchSize = 256,
    [ValidateRange(2048, 16000)]
    [int]$MinimumFreeVramMiB = 2048,
    [ValidateRange(2, 64)]
    [int]$MinimumAvailableRamGiB = 3,
    [switch]$ReplaceExistingLlama
)

$ErrorActionPreference = 'Stop'

if ($ContextSize -eq 100000) { $ContextSize = 100096 }

$modelPath = 'D:\MODELS\Ternary-Bonsai-27B-Q2_0.gguf'
$llamaBinDir = 'C:\Users\Admin\PROJECTS\llama-cpp-prism-bonsai\build-cuda-bonsai\bin\Release'
$llamaExe = Join-Path $llamaBinDir 'llama-server.exe'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stackScript = Join-Path $PSScriptRoot 'Start-WatsonStack.ps1'
$logDir = Join-Path $repoRoot '_tmp\bonsai'
$llamaPort = 8080
$backendKey = 'llama.cpp'
$modelAlias = 'bonsai27b'

function Get-CommandLineOptionValue {
    param([string]$CommandLine, [string]$Option)

    if (-not $CommandLine) { return $null }
    $pattern = '(?:^|\s)' + [regex]::Escape($Option) + '\s+(?:"([^"]*)"|(\S+))'
    $match = [regex]::Match($CommandLine, $pattern)
    if (-not $match.Success) { return $null }
    if ($match.Groups[1].Success) { return $match.Groups[1].Value }
    return $match.Groups[2].Value
}

function Get-BonsaiServerArguments {
    param(
        [string]$ModelPath,
        [int]$Port,
        [int]$ContextSize,
        [int]$BatchSize,
        [int]$UBatchSize,
        [string]$ApiKey
    )

    return @(
        '-m', $ModelPath,
        '-a', 'bonsai27b',
        '--host', '0.0.0.0',
        '--port', [string]$Port,
        '-c', [string]$ContextSize,
        '-b', [string]$BatchSize,
        '-ub', [string]$UBatchSize,
        '-ngl', '99',
        '-fa', 'on',
        '-ctk', 'q8_0',
        '-ctv', 'q8_0',
        '-dio',
        '--jinja',
        '--reasoning', 'off',
        '-np', '1',
        '--api-key', $ApiKey,
        '--metrics'
    )
}

function Test-BonsaiRuntime {
    param(
        [string]$CommandLine,
        [string]$ExpectedExecutable,
        [string]$ModelPath,
        [int]$Port,
        [int]$ContextSize,
        [int]$BatchSize,
        [int]$UBatchSize,
        [string]$ApiKey
    )

    if (-not $CommandLine) { return $false }
    $exeMatch = [regex]::Match($CommandLine, '^\s*(?:"([^"]+)"|(\S+))')
    $exe = if ($exeMatch.Groups[1].Success) { $exeMatch.Groups[1].Value } else { $exeMatch.Groups[2].Value }
    if (-not $exe -or [IO.Path]::GetFullPath($exe) -ne [IO.Path]::GetFullPath($ExpectedExecutable)) { return $false }

    foreach ($check in @(
        @{ Option = '-m'; Value = $ModelPath },
        @{ Option = '-a'; Value = 'bonsai27b' },
        @{ Option = '--port'; Value = [string]$Port },
        @{ Option = '-c'; Value = [string]$ContextSize },
        @{ Option = '-b'; Value = [string]$BatchSize },
        @{ Option = '-ub'; Value = [string]$UBatchSize },
        @{ Option = '-ngl'; Value = '99' },
        @{ Option = '-fa'; Value = 'on' },
        @{ Option = '-ctk'; Value = 'q8_0' },
        @{ Option = '-ctv'; Value = 'q8_0' },
        @{ Option = '--reasoning'; Value = 'off' },
        @{ Option = '-np'; Value = '1' },
        @{ Option = '--api-key'; Value = $ApiKey }
    )) {
        if ((Get-CommandLineOptionValue -CommandLine $CommandLine -Option $check.Option) -ne $check.Value) {
            return $false
        }
    }

    foreach ($flag in @('-dio', '--jinja', '--metrics')) {
        if ($CommandLine -notmatch '(?i)(^|\s)' + [regex]::Escape($flag) + '(?:\s|$)') { return $false }
    }
    if ($CommandLine -match '(?i)(^|\s)--skip-chat-parsing(?:\s|$)') { return $false }
    return $true
}

function Get-BonsaiStackArguments {
    param(
        [string]$StackScript,
        [string]$ModelPath,
        [string]$LlamaBinDir,
        [int]$ContextSize,
        [int]$BatchSize,
        [int]$UBatchSize
    )

    return @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $StackScript,
        '-SkipLlamaStart', '-SkipCoherenceCheck', '-SkipHermesSync', '-NoOracle',
        '-Model', 'bonsai27b', '-ServedAlias', 'bonsai27b',
        '-ModelPath', $ModelPath, '-LlamaBinDir', $LlamaBinDir,
        '-ContextSize', [string]$ContextSize,
        '-BatchSize', [string]$BatchSize,
        '-UBatchSize', [string]$UBatchSize
    )
}

function Test-ReplaceableLlamaProcess {
    param(
        [string]$ProcessName,
        [string]$ExecutablePath,
        [string]$CommandLine,
        [int]$Port,
        [string]$ProjectsRoot
    )

    if ($ProcessName -ne 'llama-server' -or -not $ExecutablePath -or -not $CommandLine) { return $false }
    if ([IO.Path]::GetFileName($ExecutablePath) -ne 'llama-server.exe') { return $false }

    $root = [IO.Path]::GetFullPath($ProjectsRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $executable = [IO.Path]::GetFullPath($ExecutablePath)
    if (-not $executable.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    return (Get-CommandLineOptionValue -CommandLine $CommandLine -Option '--port') -eq [string]$Port
}

function Get-FreeVramMiB {
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction Stop
    $value = & $nvidiaSmi.Source --query-gpu=memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or $value -notmatch '^\s*\d+\s*$') {
        throw 'Could not read free VRAM from nvidia-smi.'
    }
    return [int]$value.Trim()
}

function Get-AvailableRamBytes {
    $sample = Get-Counter '\Memory\Available Bytes' -ErrorAction Stop
    return [double]$sample.CounterSamples[0].CookedValue
}

function Test-ResourceReserve {
    param([int]$FreeVramMiB, [double]$AvailableRamBytes, [int]$MinimumFreeVramMiB)

    return ($FreeVramMiB -ge $MinimumFreeVramMiB) -and ($AvailableRamBytes -ge 2GB)
}

function Wait-ResourceReserve {
    param(
        [int]$MinimumFreeVramMiB,
        [int]$TimeoutSeconds = 60,
        [int]$RequiredHealthySamples = 2
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $healthySamples = 0
    $freeVram = 0
    $availableRam = 0.0
    $minimumFreeVram = [int]::MaxValue
    $minimumAvailableRam = [double]::MaxValue
    do {
        $freeVram = Get-FreeVramMiB
        $availableRam = Get-AvailableRamBytes
        $minimumFreeVram = [math]::Min($minimumFreeVram, $freeVram)
        $minimumAvailableRam = [math]::Min($minimumAvailableRam, $availableRam)
        if (Test-ResourceReserve -FreeVramMiB $freeVram -AvailableRamBytes $availableRam -MinimumFreeVramMiB $MinimumFreeVramMiB) {
            $healthySamples++
            if ($healthySamples -ge $RequiredHealthySamples) {
                return @{ FreeVramMiB = $freeVram; AvailableRamBytes = $availableRam }
            }
        }
        else {
            $healthySamples = 0
        }
        if ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 5 }
    } while ((Get-Date) -lt $deadline)

    throw "Resource reserve did not stay healthy for $RequiredHealthySamples samples within $TimeoutSeconds seconds (minimum VRAM free: $minimumFreeVram MiB; minimum host RAM available: $([math]::Round($minimumAvailableRam / 1GB, 2)) GiB; last sample: $freeVram MiB VRAM / $([math]::Round($availableRam / 1GB, 2)) GiB RAM)."
}

function Stop-SpawnedServer {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Seconds 1
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

foreach ($path in @($modelPath, $llamaExe, $stackScript)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required Bonsai stack file is missing: $path"
    }
}

$headroomToken = [Environment]::GetEnvironmentVariable('HEADROOM_PROXY_TOKEN', 'User')
if ([string]::IsNullOrWhiteSpace($headroomToken)) {
    throw 'HEADROOM_PROXY_TOKEN is missing from the Windows user environment; refusing to start an endpoint that cannot use authenticated Headroom.'
}

$verifyHermesConfig = @'
from pathlib import Path
from ruamel.yaml import YAML

home = Path('/home/juanbeck/.hermes')
config = YAML(typ='safe').load((home / 'config.yaml').read_text(encoding='utf-8')) or {}
model = config.get('model') or {}
provider = (config.get('providers') or {}).get('watson-llama') or {}
model_info = (provider.get('models') or {}).get('bonsai27b') or {}
headers = provider.get('extra_headers') or {}
env_lines = (home / '.env').read_text(encoding='utf-8').splitlines()
token_saved = any(line.startswith('HEADROOM_PROXY_TOKEN=') and len(line.split('=', 1)[1].strip().strip('"\'')) >= 32 for line in env_lines)
checks = [
    model.get('default') == 'bonsai27b',
    int(model.get('context_length') or 0) == int(__CONTEXT__),
    str(model.get('base_url') or '').endswith(':8787/v1'),
    provider.get('default_model') == 'bonsai27b',
    int(model_info.get('context_length') or 0) == int(__CONTEXT__),
    model_info.get('supports_vision') is False,
    headers.get('x-headroom-project-id') == 'watson-global',
    headers.get('x-headroom-user-id') == 'juanbeck',
    headers.get('x-headroom-proxy-token') == '${HEADROOM_PROXY_TOKEN}',
    token_saved,
]
print('|'.join('true' if value else 'false' for value in checks))
'@
$verifyHermesConfig = $verifyHermesConfig.Replace('__CONTEXT__', [string]$ContextSize)
$hermesConfigState = (& wsl.exe -d Ubuntu -e /home/juanbeck/.hermes/hermes-agent/venv/bin/python3 -c $verifyHermesConfig 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $hermesConfigState -ne 'true|true|true|true|true|true|true|true|true|true') {
    throw 'Hermes must already be configured for Bonsai at the requested context, with text-only capability and saved Headroom project/user/token settings.'
}

$modelsUri = "http://127.0.0.1:$llamaPort/v1/models"
$listener = Get-NetTCPConnection -State Listen -LocalPort $llamaPort -ErrorAction SilentlyContinue | Select-Object -First 1
$spawnedProcess = $null

if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process -or -not (Test-BonsaiRuntime -CommandLine $process.CommandLine -ExpectedExecutable $llamaExe -ModelPath $modelPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey)) {
        if (-not $ReplaceExistingLlama) {
            throw "Port $llamaPort is occupied by a different runtime. Nothing was stopped; pass -ReplaceExistingLlama only when intentionally switching the Watson model."
        }
        if (-not $process -or -not (Test-ReplaceableLlamaProcess -ProcessName ([IO.Path]::GetFileNameWithoutExtension($process.ExecutablePath)) -ExecutablePath $process.ExecutablePath -CommandLine $process.CommandLine -Port $llamaPort -ProjectsRoot (Split-Path $repoRoot -Parent))) {
            throw "Port $llamaPort is not owned by a llama-server.exe under the local PROJECTS directory with the expected port argument. Nothing was stopped or replaced."
        }
        Write-Warning "Stopping the explicitly selected existing llama-server (PID $($listener.OwningProcess)) before starting Bonsai."
        Stop-Process -Id $listener.OwningProcess -ErrorAction Stop
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (-not (Get-NetTCPConnection -State Listen -LocalPort $llamaPort -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Seconds 1
        }
        if (Get-NetTCPConnection -State Listen -LocalPort $llamaPort -ErrorAction SilentlyContinue) {
            throw "The previous llama-server did not release port $llamaPort. Bonsai was not started."
        }
        $listener = $null
    }
}

if ($listener) {
    try {
        $loaded = Invoke-RestMethod -Uri $modelsUri -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
    }
    catch {
        throw "The existing Bonsai process failed its authenticated model health check: $($_.Exception.Message)"
    }
    $entry = @($loaded.data | Where-Object { $_.id -eq $modelAlias } | Select-Object -First 1)
    if ($entry.Count -ne 1 -or [int]$entry[0].meta.n_ctx -ne $ContextSize) {
        throw "The existing Bonsai listener does not report context $ContextSize. Nothing was stopped or replaced."
    }
    Write-Host "Reusing validated Bonsai server: $modelAlias, context $ContextSize." -ForegroundColor Green
}
else {
    $availableRam = Get-AvailableRamBytes
    if ($availableRam -lt ([double]$MinimumAvailableRamGiB * 1GB)) {
        throw "Available host RAM is $([math]::Round($availableRam / 1GB, 2)) GiB; need at least $MinimumAvailableRamGiB GiB before loading Bonsai."
    }
    $freeVram = Get-FreeVramMiB
    $minimumStartVram = $MinimumFreeVramMiB + 10700
    if ($freeVram -lt $minimumStartVram) {
        throw "Free VRAM is $freeVram MiB; need at least $minimumStartVram MiB before loading this 100k Bonsai profile to preserve the $MinimumFreeVramMiB MiB reserve."
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $serverArgs = Get-BonsaiServerArguments -ModelPath $modelPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey
    $spawnedProcess = Start-Process -FilePath $llamaExe -WorkingDirectory $llamaBinDir -ArgumentList $serverArgs `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'llama.stdout.log') `
        -RedirectStandardError (Join-Path $logDir 'llama.stderr.log') -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Seconds 2
        $spawnedProcess.Refresh()
        if ($spawnedProcess.HasExited) {
            throw "Bonsai server exited during startup (code $($spawnedProcess.ExitCode)); inspect $logDir."
        }
        try {
            $loaded = Invoke-RestMethod -Uri $modelsUri -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 3
            $entry = @($loaded.data | Where-Object { $_.id -eq $modelAlias } | Select-Object -First 1)
            if ($entry.Count -eq 1 -and [int]$entry[0].meta.n_ctx -eq $ContextSize) {
                $ready = $true
                break
            }
        }
        catch { }
    }
    if (-not $ready) {
        Stop-SpawnedServer -ProcessId $spawnedProcess.Id
        throw "Bonsai did not become healthy at context $ContextSize within 240 seconds. See $logDir."
    }

    try {
        $null = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB
    }
    catch {
        Stop-SpawnedServer -ProcessId $spawnedProcess.Id
        throw "Bonsai loaded but failed the post-load reserve; the newly spawned process was stopped. $($_.Exception.Message)"
    }
    Write-Host "Bonsai loaded at context $ContextSize." -ForegroundColor Green
}

$null = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB

$stackArgs = Get-BonsaiStackArguments -StackScript $stackScript -ModelPath $modelPath -LlamaBinDir $llamaBinDir -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize
$proxyModels = $null
try { $proxyModels = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/v1/models' -TimeoutSec 3 } catch { }
$proxyIds = @($proxyModels.data | ForEach-Object { [string]$_.id })
if (-not ($proxyIds -contains $modelAlias) -or ($proxyIds | Where-Object { $_ -match '(?i)qwen' })) {
    $stackArgs += '-ReplaceLiteLLM'
}

& powershell.exe @stackArgs
if ($LASTEXITCODE -ne 0) {
    if ($spawnedProcess) { Stop-SpawnedServer -ProcessId $spawnedProcess.Id }
    throw "Start-WatsonStack failed to configure the validated Bonsai runtime (exit $LASTEXITCODE)."
}

$directModels = Invoke-RestMethod -Uri $modelsUri -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
$proxyModels = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/v1/models' -TimeoutSec 5
$headroomHeaders = @{ Authorization = "Bearer $headroomToken" }
$headroomModels = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/v1/models' -Headers $headroomHeaders -TimeoutSec 5
foreach ($endpoint in @(
    @{ Name = 'llama.cpp'; Models = @($directModels.data | ForEach-Object { [string]$_.id }) },
    @{ Name = 'LiteLLM'; Models = @($proxyModels.data | ForEach-Object { [string]$_.id }) },
    @{ Name = 'Headroom'; Models = @($headroomModels.data | ForEach-Object { [string]$_.id }) }
)) {
    if ($endpoint.Models -notcontains $modelAlias -or ($endpoint.Models | Where-Object { $_ -match '(?i)qwen' })) {
        throw "$($endpoint.Name) does not expose the expected Bonsai-only model catalog."
    }
}

$gatewayState = (& wsl.exe -d Ubuntu -- timeout 15 systemctl --user is-active hermes-gateway 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $gatewayState -ne 'active') {
    & wsl.exe -d Ubuntu -- /home/juanbeck/.local/bin/hermes gateway start
    if ($LASTEXITCODE -ne 0) { throw 'Hermes Gateway was not active and could not be started.' }
    $gatewayState = (& wsl.exe -d Ubuntu -- timeout 15 systemctl --user is-active hermes-gateway 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $gatewayState -ne 'active') { throw 'Hermes Gateway did not become active.' }
}

$reserve = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB
Write-Host "Bonsai stack is ready for Hermes. Stable reserve: $($reserve.FreeVramMiB) MiB VRAM and $([math]::Round($reserve.AvailableRamBytes / 1GB, 2)) GiB host RAM. No inference was run." -ForegroundColor Green
