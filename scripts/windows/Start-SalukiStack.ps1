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
    [int]$MinimumAvailableRamGiB = 3
)

$ErrorActionPreference = 'Stop'

function Get-CommandLineOptionValue {
    param([string]$CommandLine, [string]$Option)

    if (-not $CommandLine) { return $null }
    $pattern = '(?:^|\s)' + [regex]::Escape($Option) + '\s+(?:"([^"]*)"|(\S+))'
    $match = [regex]::Match($CommandLine, $pattern)
    if (-not $match.Success) { return $null }
    if ($match.Groups[1].Success) { return $match.Groups[1].Value }
    return $match.Groups[2].Value
}

function Get-SalukiServerArguments {
    param(
        [string]$ModelPath,
        [string]$MmprojPath,
        [int]$Port,
        [int]$ContextSize,
        [int]$BatchSize,
        [int]$UBatchSize,
        [string]$ApiKey
    )

    return @(
        '-m', $ModelPath,
        '-a', 'saluki27b',
        '--host', '0.0.0.0',
        '--port', [string]$Port,
        '-c', [string]$ContextSize,
        '-b', [string]$BatchSize,
        '-ub', [string]$UBatchSize,
        '-ngl', '99',
        '-fa', 'on',
        '-ctk', 'q8_0',
        '-ctv', 'q8_0',
        '-mm', $MmprojPath,
        '--mmproj-offload',
        '--jinja',
        '--reasoning', 'off',
        '-np', '1',
        '-cram', '0',
        '-lm', 'dio',
        '--api-key', $ApiKey,
        '--metrics'
    )
}

function Test-SalukiRuntime {
    param(
        [string]$CommandLine,
        [string]$ExpectedExecutable,
        [string]$ModelPath,
        [string]$MmprojPath,
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
        @{ Option = '-a'; Value = 'saluki27b' },
        @{ Option = '--port'; Value = [string]$Port },
        @{ Option = '-c'; Value = [string]$ContextSize },
        @{ Option = '-b'; Value = [string]$BatchSize },
        @{ Option = '-ub'; Value = [string]$UBatchSize },
        @{ Option = '-ngl'; Value = '99' },
        @{ Option = '-fa'; Value = 'on' },
        @{ Option = '-ctk'; Value = 'q8_0' },
        @{ Option = '-ctv'; Value = 'q8_0' },
        @{ Option = '-mm'; Value = $MmprojPath },
        @{ Option = '--reasoning'; Value = 'off' },
        @{ Option = '-np'; Value = '1' },
        @{ Option = '-cram'; Value = '0' },
        @{ Option = '-lm'; Value = 'dio' },
        @{ Option = '--api-key'; Value = $ApiKey }
    )) {
        if ((Get-CommandLineOptionValue -CommandLine $CommandLine -Option $check.Option) -ne $check.Value) {
            return $false
        }
    }

    foreach ($flag in @('--mmproj-offload', '--jinja', '--metrics')) {
        if ($CommandLine -notmatch '(?i)(^|\s)' + [regex]::Escape($flag) + '(?:\s|$)') { return $false }
    }
    if ($CommandLine -match '(?i)(^|\s)--skip-chat-parsing(?:\s|$)') { return $false }
    return $true
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
        [int]$TimeoutSeconds = 30,
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

    throw "Resource reserve did not stay healthy for $RequiredHealthySamples samples within $TimeoutSeconds seconds (minimum observed VRAM free: $minimumFreeVram MiB; host RAM available: $([math]::Round($minimumAvailableRam / 1GB, 2)) GiB; last sample: $freeVram MiB VRAM / $([math]::Round($availableRam / 1GB, 2)) GiB RAM)."
}

function Stop-SpawnedServer {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try { & taskkill.exe /PID $ProcessId /T /F *> $null } catch { }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if ($ContextSize -eq 100000) { $ContextSize = 100096 }

$modelPath = 'D:\MODELS\Underdog-Saluki-27B-1.0-IQ2-mix.gguf'
$mmprojPath = 'D:\MODELS\mmproj-Underdog-Saluki-27B-1.0-F16.gguf'
$llamaBinDir = 'C:\Users\Admin\PROJECTS\llama-b11433-win-cuda134\llama'
$llamaExe = Join-Path $llamaBinDir 'llama-server.exe'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stackScript = Join-Path $PSScriptRoot 'Start-WatsonStack.ps1'
$logDir = Join-Path $repoRoot '_tmp\saluki'
$llamaPort = 8080
$backendKey = 'llama.cpp'

foreach ($path in @($modelPath, $mmprojPath, $llamaExe, $stackScript)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required Saluki stack file is missing: $path"
    }
}

if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('HEADROOM_PROXY_TOKEN', 'User'))) {
    throw 'HEADROOM_PROXY_TOKEN is missing from the Windows user environment; refusing to load a model that cannot use the authenticated proxy route.'
}

$verifyHermesConfig = @'
from pathlib import Path
from ruamel.yaml import YAML

home = Path('/home/juanbeck/.hermes')
config = YAML(typ='safe').load((home / 'config.yaml').read_text(encoding='utf-8')) or {}
model = config.get('model') or {}
provider = (config.get('providers') or {}).get('watson-llama') or {}
model_info = (provider.get('models') or {}).get('saluki27b') or {}
headers = provider.get('extra_headers') or {}
env_lines = (home / '.env').read_text(encoding='utf-8').splitlines()
token_saved = any(line.startswith('HEADROOM_PROXY_TOKEN=') and len(line.split('=', 1)[1].strip().strip('"\'')) >= 32 for line in env_lines)
checks = [
    model.get('default') == 'saluki27b',
    int(model.get('context_length') or 0) == int(__CONTEXT__),
    int(model_info.get('context_length') or 0) == int(__CONTEXT__),
    model_info.get('supports_vision') is True,
    headers.get('x-headroom-project-id') == 'watson-global',
    headers.get('x-headroom-user-id') == 'juanbeck',
    headers.get('x-headroom-proxy-token') == '${HEADROOM_PROXY_TOKEN}',
    token_saved,
]
print('|'.join('true' if value else 'false' for value in checks))
'@
$verifyHermesConfig = $verifyHermesConfig.Replace('__CONTEXT__', [string]$ContextSize)
$hermesConfigState = (& wsl.exe -d Ubuntu -e /home/juanbeck/.hermes/hermes-agent/venv/bin/python3 -c $verifyHermesConfig 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $hermesConfigState -ne 'true|true|true|true|true|true|true|true') {
    throw 'Hermes must be configured for Saluki at the requested context, with vision, project/user memory headers, and a saved Headroom token before this launcher runs.'
}

$modelsUri = "http://127.0.0.1:$llamaPort/v1/models"
$listener = Get-NetTCPConnection -State Listen -LocalPort $llamaPort -ErrorAction SilentlyContinue | Select-Object -First 1
$spawnedProcess = $null

if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process -or -not (Test-SalukiRuntime -CommandLine $process.CommandLine -ExpectedExecutable $llamaExe -ModelPath $modelPath -MmprojPath $mmprojPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey)) {
        throw "Port $llamaPort is occupied by a different llama.cpp runtime. Nothing was stopped or replaced."
    }

    try {
        $loaded = Invoke-RestMethod -Uri $modelsUri -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
    }
    catch {
        throw "The existing Saluki process does not pass its authenticated model health check: $($_.Exception.Message)"
    }

    $entry = @($loaded.data | Where-Object { $_.id -eq 'saluki27b' } | Select-Object -First 1)
    if ($entry.Count -ne 1 -or [int]$entry[0].meta.n_ctx -ne $ContextSize) {
        throw "The existing Saluki listener does not report context $ContextSize. Nothing was stopped or replaced."
    }
    Write-Host "Reusing validated Saluki server: $($entry[0].id), context $($entry[0].meta.n_ctx)." -ForegroundColor Green
}
else {
    $availableRam = Get-AvailableRamBytes
    $minimumRamBytes = [double]$MinimumAvailableRamGiB * 1GB
    if ($availableRam -lt $minimumRamBytes) {
        throw "Available host RAM is $([math]::Round($availableRam / 1GB, 2)) GiB; need at least $MinimumAvailableRamGiB GiB before loading Saluki."
    }

    $freeVram = Get-FreeVramMiB
    $minimumStartVram = $MinimumFreeVramMiB + 12673
    if ($freeVram -lt $minimumStartVram) {
        throw "Free VRAM is $freeVram MiB; need at least $minimumStartVram MiB before loading this 100k profile to preserve the $MinimumFreeVramMiB MiB reserve."
    }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $serverArgs = Get-SalukiServerArguments -ModelPath $modelPath -MmprojPath $mmprojPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey
    $spawnedProcess = Start-Process -FilePath $llamaExe -WorkingDirectory $llamaBinDir -ArgumentList $serverArgs `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'llama.stdout.log') `
        -RedirectStandardError (Join-Path $logDir 'llama.stderr.log') -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 2
        $spawnedProcess.Refresh()
        if ($spawnedProcess.HasExited) {
            throw "Saluki server exited during startup (code $($spawnedProcess.ExitCode)); inspect $logDir."
        }
        try {
            $loaded = Invoke-RestMethod -Uri $modelsUri -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 3
            $entry = @($loaded.data | Where-Object { $_.id -eq 'saluki27b' } | Select-Object -First 1)
            if ($entry.Count -eq 1 -and [int]$entry[0].meta.n_ctx -eq $ContextSize) {
                $ready = $true
                break
            }
        }
        catch { }
    }

    if (-not $ready) {
        Stop-SpawnedServer -ProcessId $spawnedProcess.Id
        throw "Saluki did not become healthy with context $ContextSize within 120 seconds."
    }

    try {
        $reserve = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB
        $freeVram = $reserve.FreeVramMiB
        $availableRam = $reserve.AvailableRamBytes
    }
    catch {
        Stop-SpawnedServer -ProcessId $spawnedProcess.Id
        throw "Post-load reserve failed. The new Saluki process was stopped. $($_.Exception.Message)"
    }

    Write-Host "Saluki loaded at context $ContextSize; free VRAM $freeVram MiB; host available RAM $([math]::Round($availableRam / 1GB, 2)) GiB." -ForegroundColor Green
}

$reserve = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB
$freeVram = $reserve.FreeVramMiB
$availableRam = $reserve.AvailableRamBytes

$stackArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stackScript,
    '-SkipLlamaStart', '-SkipCoherenceCheck', '-SkipHermesSync',
    '-Model', 'saluki27b', '-ServedAlias', 'saluki27b',
    '-ModelPath', $modelPath, '-LlamaBinDir', $llamaBinDir,
    '-ContextSize', $ContextSize, '-BatchSize', $BatchSize, '-UBatchSize', $UBatchSize
)

$proxyModels = $null
try { $proxyModels = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/v1/models' -TimeoutSec 3 } catch { }
if (-not (@($proxyModels.data | ForEach-Object { $_.id }) -contains 'saluki27b')) {
    $stackArgs += '-ReplaceLiteLLM'
}

& powershell.exe @stackArgs
if ($LASTEXITCODE -ne 0) {
    throw "Start-WatsonStack failed to configure the validated Saluki runtime (exit $LASTEXITCODE)."
}

$reserve = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB
$freeVram = $reserve.FreeVramMiB
$availableRam = $reserve.AvailableRamBytes

Write-Host "Saluki stack is ready. Stable reserve: $freeVram MiB VRAM, $([math]::Round($availableRam / 1GB, 2)) GiB host RAM. Qwen3.8 was not loaded or advertised." -ForegroundColor Green
