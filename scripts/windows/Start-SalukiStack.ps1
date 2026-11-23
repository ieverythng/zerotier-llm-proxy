[CmdletBinding()]
param(
    [ValidateRange(32768, 262144)]
    [int]$ContextSize = 100096,
    [ValidateRange(128, 2048)]
    [int]$BatchSize = 512,
    [ValidateRange(64, 1024)]
    [int]$UBatchSize = 256,
    [ValidateRange(2048, 16000)]
    [int]$MinimumFreeVramMiB = 2048,
    [ValidateRange(2, 64)]
    [int]$MinimumAvailableRamGiB = 3,
    [ValidateRange(0, 99)]
    [int]$MaximumGpuUtilizationPercent = 92,
    [ValidateRange(1, 99)]
    [int]$GpuLayerCount = 99,
    [ValidateSet('q4_0', 'q8_0')]
    [string]$KVCacheType = 'q8_0'
)

$ErrorActionPreference = 'Stop'

function Get-NormalizedContextSize {
    param(
        [ValidateRange(32768, 262144)]
        [int]$ContextSize
    )

    return [int]([math]::Ceiling($ContextSize / 256.0) * 256)
}

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
        [string]$ApiKey,
        [ValidateRange(1, 99)]
        [int]$GpuLayerCount = 99,
        [ValidateSet('q4_0', 'q8_0')]
        [string]$KVCacheType = 'q8_0'
    )

    return @(
        '-m', $ModelPath,
        '-a', 'saluki27b',
        '--host', '0.0.0.0',
        '--port', [string]$Port,
        '-c', [string]$ContextSize,
        '-b', [string]$BatchSize,
        '-ub', [string]$UBatchSize,
        '-ngl', [string]$GpuLayerCount,
        '-fa', 'on',
        '-ctk', $KVCacheType,
        '-ctv', $KVCacheType,
        '-mm', $MmprojPath,
        '--mmproj-offload',
        '--image-min-tokens', '1024',
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
        [string]$ApiKey,
        [ValidateRange(1, 99)]
        [int]$GpuLayerCount = 99,
        [ValidateSet('q4_0', 'q8_0')]
        [string]$KVCacheType = 'q8_0'
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
        @{ Option = '-ngl'; Value = [string]$GpuLayerCount },
        @{ Option = '-fa'; Value = 'on' },
        @{ Option = '-ctk'; Value = $KVCacheType },
        @{ Option = '-ctv'; Value = $KVCacheType },
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

    foreach ($flag in @('--mmproj-offload', '--jinja', '--metrics', '--image-min-tokens')) {
        if ($CommandLine -notmatch '(?i)(^|\s)' + [regex]::Escape($flag) + '(?:\s|$)') { return $false }
    }
    if ((Get-CommandLineOptionValue -CommandLine $CommandLine -Option '--image-min-tokens') -ne '1024') { return $false }
    if ($CommandLine -match '(?i)(^|\s)--skip-chat-parsing(?:\s|$)') { return $false }
    return $true
}

function Get-FreeVramMiB {
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction Stop
    $output = @(& $nvidiaSmi.Source --query-gpu=memory.free --format=csv,noheader,nounits 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Could not read free VRAM from nvidia-smi.' }
    return ConvertFrom-NvidiaSmiMetric -Output $output -MetricName 'free VRAM'
}

function Get-AvailableRamBytes {
    $sample = Get-Counter '\Memory\Available Bytes' -ErrorAction Stop
    return [double]$sample.CounterSamples[0].CookedValue
}

function Get-GpuUtilizationPercent {
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction Stop
    $output = @(& $nvidiaSmi.Source --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Could not read GPU utilization from nvidia-smi.' }
    return ConvertFrom-NvidiaSmiMetric -Output $output -MetricName 'GPU utilization'
}

function ConvertFrom-NvidiaSmiMetric {
    param([string[]]$Output, [string]$MetricName)

    $value = @($Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    if ($value.Count -ne 1 -or $value[0] -notmatch '^\s*\d+\s*$') {
        throw "Could not parse $MetricName from nvidia-smi."
    }
    return [int]$value[0].Trim()
}

function Test-ResourceReserve {
    param(
        [int]$FreeVramMiB,
        [double]$AvailableRamBytes,
        [int]$MinimumFreeVramMiB,
        [int]$MinimumAvailableRamGiB = 2,
        [int]$GpuUtilizationPct = 0,
        [int]$MaximumGpuUtilizationPct = 92
    )

    return ($FreeVramMiB -ge $MinimumFreeVramMiB) -and
        ($AvailableRamBytes -ge ([double]$MinimumAvailableRamGiB * 1GB)) -and
        ($GpuUtilizationPct -le $MaximumGpuUtilizationPct)
}

function Wait-ResourceReserve {
    param(
        [int]$MinimumFreeVramMiB,
        # WSL can retain its initial VM working set briefly after the config
        # probe exits; allow that reclaim cycle to finish before failing closed.
        [int]$TimeoutSeconds = 60,
        [int]$RequiredHealthySamples = 2,
        [int]$MaximumGpuUtilizationPct = 92,
        [int]$MinimumAvailableRamGiB = 2
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $healthySamples = 0
    $freeVram = 0
    $availableRam = 0.0
    $gpuUtilization = 100
    $minimumFreeVram = [int]::MaxValue
    $minimumAvailableRam = [double]::MaxValue
    $maximumGpuUtilization = 0
    do {
        $freeVram = Get-FreeVramMiB
        $availableRam = Get-AvailableRamBytes
        $gpuUtilization = Get-GpuUtilizationPercent
        $minimumFreeVram = [math]::Min($minimumFreeVram, $freeVram)
        $minimumAvailableRam = [math]::Min($minimumAvailableRam, $availableRam)
        $maximumGpuUtilization = [math]::Max($maximumGpuUtilization, $gpuUtilization)
        if (Test-ResourceReserve -FreeVramMiB $freeVram -AvailableRamBytes $availableRam -MinimumFreeVramMiB $MinimumFreeVramMiB -MinimumAvailableRamGiB $MinimumAvailableRamGiB -GpuUtilizationPct $gpuUtilization -MaximumGpuUtilizationPct $MaximumGpuUtilizationPct) {
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

    throw "Resource reserve did not stay healthy for $RequiredHealthySamples samples within $TimeoutSeconds seconds (minimum VRAM free: $minimumFreeVram MiB; minimum host RAM: $([math]::Round($minimumAvailableRam / 1GB, 2)) GiB; maximum GPU utilization: $maximumGpuUtilization%; last sample: $freeVram MiB VRAM / $([math]::Round($availableRam / 1GB, 2)) GiB RAM / $gpuUtilization% GPU)."
}

function Stop-SpawnedServer {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try { & taskkill.exe /PID $ProcessId /T /F *> $null } catch { }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-FinalResourceGate {
    param(
        [int]$MinimumFreeVramMiB,
        [int]$MaximumGpuUtilizationPercent,
        [int]$ProcessId
    )

    try {
        return Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB -MaximumGpuUtilizationPct $MaximumGpuUtilizationPercent
    }
    catch {
        if ($ProcessId -gt 0) { Stop-SpawnedServer -ProcessId $ProcessId }
        throw
    }
}

$ContextSize = Get-NormalizedContextSize -ContextSize $ContextSize

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
$litellmExecutable = Join-Path $repoRoot '.runtime\litellm\.venv\Scripts\litellm.exe'
$litellmRuntimeScript = Join-Path $PSScriptRoot 'Start-Qwen36LiteLLM.ps1'
& $litellmRuntimeScript -ValidateRuntimeOnly -EnsureMasterKey -LiteLLMExecutable $litellmExecutable

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
token_saved = any(line.startswith('HEADROOM_PROXY_TOKEN=') and len(line.split('=', 1)[1].strip().strip(chr(34) + chr(39))) >= 32 for line in env_lines)
checks = [
    model_info.get('supports_vision') is True,
    headers.get('x-headroom-project-id') == 'watson-global',
    headers.get('x-headroom-user-id') == 'juanbeck',
    headers.get('x-headroom-proxy-token') == '${HEADROOM_PROXY_TOKEN}',
    token_saved,
    str(model.get('base_url') or '').rstrip('/').endswith(':8787/v1'),
]
print('|'.join('true' if value else 'false' for value in checks))
'@
$hermesConfigState = (& wsl.exe -d Ubuntu -e /home/juanbeck/.hermes/hermes-agent/venv/bin/python3 -c $verifyHermesConfig 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $hermesConfigState -ne 'true|true|true|true|true|true') {
    throw 'Hermes must have the Saluki vision catalog, Headroom route, project/user memory headers, and saved proxy token before this launcher runs.'
}

$modelsUri = "http://127.0.0.1:$llamaPort/v1/models"
$listener = Get-NetTCPConnection -State Listen -LocalPort $llamaPort -ErrorAction SilentlyContinue | Select-Object -First 1
$spawnedProcess = $null

if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process -or -not (Test-SalukiRuntime -CommandLine $process.CommandLine -ExpectedExecutable $llamaExe -ModelPath $modelPath -MmprojPath $mmprojPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey -GpuLayerCount $GpuLayerCount -KVCacheType $KVCacheType)) {
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
    # The existing 100k Q8 profile is the conservative cold-start reference.
    # A longer Q4 profile has a smaller KV allocation than that reference.
    $minimumStartVram = $MinimumFreeVramMiB + 12673
    $preflight = Wait-ResourceReserve -MinimumFreeVramMiB $minimumStartVram -TimeoutSeconds 90 `
        -MaximumGpuUtilizationPct $MaximumGpuUtilizationPercent -MinimumAvailableRamGiB $MinimumAvailableRamGiB
    $freeVram = $preflight.FreeVramMiB
    $availableRam = $preflight.AvailableRamBytes

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $serverArgs = Get-SalukiServerArguments -ModelPath $modelPath -MmprojPath $mmprojPath -Port $llamaPort -ContextSize $ContextSize -BatchSize $BatchSize -UBatchSize $UBatchSize -ApiKey $backendKey -GpuLayerCount $GpuLayerCount -KVCacheType $KVCacheType
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
        $reserve = Wait-ResourceReserve -MinimumFreeVramMiB $MinimumFreeVramMiB -MaximumGpuUtilizationPct $MaximumGpuUtilizationPercent
        $freeVram = $reserve.FreeVramMiB
        $availableRam = $reserve.AvailableRamBytes
    }
    catch {
        Stop-SpawnedServer -ProcessId $spawnedProcess.Id
        throw "Post-load reserve failed. The new Saluki process was stopped. $($_.Exception.Message)"
    }

    Write-Host "Saluki loaded at context $ContextSize; free VRAM $freeVram MiB; host available RAM $([math]::Round($availableRam / 1GB, 2)) GiB." -ForegroundColor Green
}

try {
    $reserve = Invoke-FinalResourceGate -MinimumFreeVramMiB $MinimumFreeVramMiB -MaximumGpuUtilizationPercent $MaximumGpuUtilizationPercent -ProcessId $(if ($spawnedProcess) { $spawnedProcess.Id } else { 0 })
}
catch {
    throw "Final resource reserve failed; a Saluki process started by this run was stopped when needed. $($_.Exception.Message)"
}
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
try {
    $proxyModels = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/v1/models' `
        -Headers @{ Authorization = "Bearer $env:LITELLM_MASTER_KEY" } -TimeoutSec 3
} catch { }
if (-not (@($proxyModels.data | ForEach-Object { $_.id }) -contains 'saluki27b')) {
    $stackArgs += '-ReplaceLiteLLM'
}

& powershell.exe @stackArgs
if ($LASTEXITCODE -ne 0) {
    throw "Start-WatsonStack failed to configure the validated Saluki runtime (exit $LASTEXITCODE)."
}

$setHermesProfile = Join-Path $PSScriptRoot 'Set-HermesSalukiProfile.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setHermesProfile -ContextWindow $ContextSize
if ($LASTEXITCODE -ne 0) {
    throw "Saluki is loaded, but Hermes did not switch to the Saluki $ContextSize-token profile (exit $LASTEXITCODE)."
}

$wslRoutes = & wsl.exe -d Ubuntu -- ip -j -4 route show default
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the Windows host gateway from WSL for the Hermes route check.' }
$wslHostAddress = ($wslRoutes | ConvertFrom-Json | Select-Object -First 1).gateway
$headroomHealthUrl = "http://${wslHostAddress}:8787/health"
& wsl.exe -d Ubuntu -- bash -lc "curl -fsS --max-time 5 '$headroomHealthUrl' >/dev/null"
if ($LASTEXITCODE -ne 0) {
    throw "WSL/Hermes cannot reach Headroom at $headroomHealthUrl; Discord requests would not be ready."
}

$reserve = Invoke-FinalResourceGate -MinimumFreeVramMiB $MinimumFreeVramMiB -MaximumGpuUtilizationPercent $MaximumGpuUtilizationPercent -ProcessId $(if ($spawnedProcess) { $spawnedProcess.Id } else { 0 })
$freeVram = $reserve.FreeVramMiB
$availableRam = $reserve.AvailableRamBytes

Write-Host "Saluki stack is ready. Stable reserve: $freeVram MiB VRAM, $([math]::Round($availableRam / 1GB, 2)) GiB host RAM. Qwen3.8 was not loaded or advertised." -ForegroundColor Green
