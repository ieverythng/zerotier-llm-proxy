# Start-WatsonStack.ps1
# =============================================
# Unified startup script for the Watson ZeroTier LLM Proxy stack.
# Launches: llama.cpp → LiteLLM proxy. webchat2api is opt-in.
#
# Usage:
#   .\Start-WatsonStack.ps1
#   .\Start-WatsonStack.ps1 -Model qwen3.8 -ContextSize 100096
#   .\Start-WatsonStack.ps1 -ModelPath "D:\MODELS\other-model.gguf"
#   .\Start-WatsonStack.ps1 -EnableOracle
# =============================================

[CmdletBinding()]
param(
    [string]$LlamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server",
    [string]$LlamaScript = "scripts\start_profile.ps1",
    [string]$LlamaBinDir = "",
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Model = "qwen3.8",
    # The served alias is the llama.cpp model id. Set it explicitly when
    # selecting a custom model; the default is the canonical Qwen3.8 name.
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$ServedAlias = "",
    [string]$Profile = "hermes-qwen38-100k",
    # llama.cpp rounds -c 100000 to n_ctx=100096 for the selected GGUF.
    [int]$ContextSize = 100096,
    [int]$BatchSize = 0,
    [int]$UBatchSize = 0,
    [string]$ModelDirectory = "D:\MODELS",
    [string]$ModelPath = "",
    [string]$BackendKey = "llama.cpp",
    [switch]$Metrics,
    [switch]$SkipChatParsing,
    [switch]$SkipCoherenceCheck,
    [switch]$SkipLlamaStart,
    [switch]$ReplaceLiteLLM,
    [switch]$NoHeadroom,
    [switch]$RouteHermesThroughHeadroom,
    [int]$HeadroomPort = 8787,
    [switch]$SkipHermesSync,
    [switch]$ForceHeadroomCompression,
    [switch]$EnableOracle,
    [switch]$NoOracle,
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [int]$Webchat2ApiPort = 9000
)

$ErrorActionPreference = "Stop"

# llama.cpp aligns this model's requested 100,000-token allocation to
# n_ctx=100,096. Normalize the common human-facing value before comparing
# health metadata so a CLI request for 100k does not fail its own readiness
# check after llama.cpp performs that alignment.
if ($ContextSize -eq 100000) {
    $ContextSize = 100096
}
$oracleEnabled = $EnableOracle -and -not $NoOracle
$headroomEnabled = -not $NoHeadroom
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$litellmExecutable = Join-Path $repoRoot '.runtime\litellm\.venv\Scripts\litellm.exe'
$litellmRuntimeScript = Join-Path $PSScriptRoot 'Start-Qwen36LiteLLM.ps1'
if (-not (Test-Path -LiteralPath $litellmRuntimeScript -PathType Leaf)) {
    throw "LiteLLM runtime validator not found: $litellmRuntimeScript"
}
& $litellmRuntimeScript -ValidateRuntimeOnly -EnsureMasterKey -LiteLLMExecutable $litellmExecutable
$litellmMasterKey = $env:LITELLM_MASTER_KEY

# ─── Color helpers ──────────────────────────────────────────────
function Write-Step { param([string]$Msg); Write-Host ("`n[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg); Write-Host ("  [OK] {0}" -f $Msg) -ForegroundColor Green }
function Write-Warn { param([string]$Msg); Write-Host ("  [WARN] {0}" -f $Msg) -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg); Write-Host ("  [FAIL] {0}" -f $Msg) -ForegroundColor Red }

# ─── Health check helper ────────────────────────────────────────
function Test-JsonEndpoint {
    param(
        [string]$Uri,
        [string]$ApiKey = ""
    )
    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
        $headers.Authorization = "Bearer $ApiKey"
    }
    try { return Invoke-RestMethod -Uri $Uri -Headers $headers -Method Get -TimeoutSec 5 -MaximumRedirection 0 }
    catch { return $null }
}

function Test-PortListening {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object LocalPort -eq $Port | Select-Object -First 1)
}

function Test-NvidiaHealth {
    $nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $nvidia) { return $false }
    $stdout = [IO.Path]::GetTempFileName()
    $stderr = [IO.Path]::GetTempFileName()
    try {
        $probe = Start-Process -FilePath $nvidia.Source `
            -ArgumentList '--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.free', '--format=csv,noheader,nounits' `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        if (-not $probe.WaitForExit(5000)) {
            try { $probe.Kill() } catch { }
            return $false
        }
        $probe.Refresh()
        $exitCode = $probe.ExitCode
        $probeOutput = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
        if ($null -ne $exitCode -and $exitCode -ne 0) { return $false }
        return -not [string]::IsNullOrWhiteSpace($probeOutput)
    }
    catch { return $false }
    finally {
        Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
    }
}

function Get-SingleListenerProcess {
    param([int]$Port)
    $owners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object LocalPort -eq $Port | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -eq 1 -and $owners[0] -gt 0) {
        return Get-CimInstance Win32_Process -Filter "ProcessId = $($owners[0])" -ErrorAction SilentlyContinue
    }
}

function Test-LiteLLMListener {
    param([int]$Port, [string]$ExpectedExecutable)
    $process = Get-SingleListenerProcess -Port $Port
    # The Python console entry point can have a child own the listening socket.
    for ($depth = 0; $process -and $depth -lt 4; $depth++) {
        if ($process.ExecutablePath -eq $ExpectedExecutable) { return $true }
        if (-not $process.ParentProcessId) { break }
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
        if (-not $parent.CreationDate -or -not $process.CreationDate -or $parent.CreationDate -gt $process.CreationDate) { break }
        $process = $parent
    }
    return $false
}

function Confirm-ExistingLiteLLM {
    param([int]$Port, [string]$ExpectedExecutable, [string]$BaseUrl, [string]$ApiKey, [string]$ModelName, [switch]$Replace)

    if ($Replace) {
        throw "-ReplaceLiteLLM cannot stop an existing listener on port $Port. Stop it through its owner or use a free port."
    }
    if (-not (Test-LiteLLMListener -Port $Port -ExpectedExecutable $ExpectedExecutable)) {
        throw "LiteLLM port $Port is occupied by an unverified listener; no process was stopped."
    }
    $catalog = Test-JsonEndpoint -Uri "$BaseUrl/models" -ApiKey $ApiKey
    if (-not $catalog) {
        throw "LiteLLM listener on port $Port did not pass its authenticated model check; no process was stopped."
    }
    if (@($catalog.data.id) -notcontains $ModelName) {
        throw "Running LiteLLM does not list '$ModelName'. Stop it through its owner or use a free port; no process was stopped."
    }
}

function Get-LoadedModelContextSize {
    param($Models, [string]$ModelName)

    $entry = @($Models.data | Where-Object { $_.id -eq $ModelName } | Select-Object -First 1)
    if ($entry.Count -eq 1 -and $null -ne $entry[0].meta.n_ctx) {
        return [int]$entry[0].meta.n_ctx
    }
    return $null
}

function Resolve-DefaultModelPath {
    param(
        [string]$RequestedPath,
        [string]$Root,
        [string]$CanonicalModel
    )

    if ($RequestedPath) {
        return (Resolve-Path -LiteralPath $RequestedPath).Path
    }

    if ($CanonicalModel -eq "qwen3.8") {
        $candidates = @(Get-ChildItem -LiteralPath $Root -File -Filter "Qwen3.8-*.gguf" -ErrorAction Stop)
        if ($candidates.Count -eq 1) {
            return $candidates[0].FullName
        }
        if ($candidates.Count -eq 0) {
            throw "No Qwen3.8 GGUF found in '$Root'. Pass -ModelPath explicitly."
        }
        throw "Expected exactly one Qwen3.8 GGUF in '$Root', found $($candidates.Count). Pass -ModelPath explicitly."
    }

    return ""
}

$ModelPath = Resolve-DefaultModelPath -RequestedPath $ModelPath -Root $ModelDirectory -CanonicalModel $Model
if ($Model -ne "qwen3.8" -and -not $ModelPath -and $Profile -eq "hermes-qwen38-100k") {
    throw "A non-Qwen3.8 model requires -ModelPath or a profile with its own model_path."
}
if (-not $ServedAlias) {
    $ServedAlias = $Model
}
if (-not $LlamaBinDir -and $Model -eq 'qwen3.8') {
    # This is the CUDA build that passed the IQ3_S coherence/context gates.
    # The old TurboQuant and local d08c787 builds produce corrupt output.
    $LlamaBinDir = 'C:\Users\Admin\PROJECTS\llama-b10621-win-cuda133'
}
if ($LlamaBinDir) {
    $LlamaBinDir = (Resolve-Path -LiteralPath $LlamaBinDir).Path
    if (-not (Test-Path -LiteralPath (Join-Path $LlamaBinDir 'llama-server.exe'))) {
        throw "llama-server.exe is missing from '$LlamaBinDir'."
    }
    if ([IO.Path]::GetFileName($LlamaScript) -eq 'start_turbo_hermes.ps1') {
        throw 'Use scripts\start_profile.ps1 with -LlamaBinDir; the TurboQuant wrapper overwrites its target binary directory.'
    }
}

function Get-LlamaServerCommandLine {
    $process = Get-SingleListenerProcess -Port $LlamaPort
    if (-not $process.ExecutablePath -or [IO.Path]::GetFileName($process.ExecutablePath) -ne 'llama-server.exe') { return '' }
    if ($LlamaBinDir -and $process.ExecutablePath -ne (Join-Path $LlamaBinDir 'llama-server.exe')) { return '' }
    return [string]$process.CommandLine
}

function Get-CommandLineOptionValue {
    param(
        [string]$CommandLine,
        [string]$Option
    )

    if (-not $CommandLine) { return $null }
    $pattern = '(?:^|\s)' + [regex]::Escape($Option) + '\s+(?:"([^"]*)"|(\S+))'
    $match = [regex]::Match($CommandLine, $pattern)
    if (-not $match.Success) { return $null }
    if ($match.Groups[1].Success) { return $match.Groups[1].Value }
    return $match.Groups[2].Value
}

function Test-RequestedLlamaRuntime {
    param(
        [string]$CommandLine,
        [int]$RequestedBatchSize,
        [int]$RequestedUBatchSize,
        [string]$RequestedModelPath,
        [bool]$RequestedSkipChatParsing,
        [string]$RequestedBinDir
    )

    if ($RequestedBatchSize -gt 0 -and [int](Get-CommandLineOptionValue -CommandLine $CommandLine -Option '-b') -ne $RequestedBatchSize) {
        return $false
    }
    if ($RequestedUBatchSize -gt 0 -and [int](Get-CommandLineOptionValue -CommandLine $CommandLine -Option '-ub') -ne $RequestedUBatchSize) {
        return $false
    }
    if ($RequestedModelPath) {
        $loadedModelPath = Get-CommandLineOptionValue -CommandLine $CommandLine -Option '-m'
        if (-not $loadedModelPath) { return $false }
        if ([IO.Path]::GetFullPath($loadedModelPath) -ne [IO.Path]::GetFullPath($RequestedModelPath)) { return $false }
    }
    $hasSkipChatParsing = $CommandLine -match '(?i)(^|\s)--skip-chat-parsing(?:\s|$)'
    if ($RequestedSkipChatParsing -ne $hasSkipChatParsing) { return $false }
    if ($RequestedBinDir) {
        $executable = [regex]::Match($CommandLine, '^\s*(?:"([^"]+)"|(\S+))')
        $loadedExe = if ($executable.Groups[1].Success) { $executable.Groups[1].Value } else { $executable.Groups[2].Value }
        if (-not $loadedExe -or [IO.Path]::GetFullPath($loadedExe) -ne (Join-Path $RequestedBinDir 'llama-server.exe')) { return $false }
    }
    return $true
}

function Invoke-PowerShellScript {
    param([object[]]$ArgumentList)

    & powershell.exe @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell child process failed with exit code $LASTEXITCODE."
    }
}

# ─── Phase 0: Banner ────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "       ZeroTier LLM Proxy Stack - Startup Script" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host ""

# ─── Phase 1: llama.cpp ─────────────────────────────────────────
$llamaBaseUrl = "http://127.0.0.1:$LlamaPort/v1"

if (-not $SkipLlamaStart) {
    Write-Step "Phase 1: Starting llama.cpp server"

    $resolvedLlamaRepo = Resolve-Path -LiteralPath $LlamaRepo
    $resolvedLlamaScript = Join-Path $resolvedLlamaRepo $LlamaScript

    if (-not (Test-Path -LiteralPath $resolvedLlamaScript)) {
        Write-Fail "llama.cpp startup script not found: $resolvedLlamaScript"
        throw "Cannot proceed without llama.cpp startup script."
    }

    if (Test-PortListening -Port $LlamaPort) {
        $existingCommandLine = Get-LlamaServerCommandLine
        if (-not $existingCommandLine) {
            throw "llama.cpp port $LlamaPort is occupied by an unverified listener; no process was stopped."
        }
        $existingModels = Test-JsonEndpoint -Uri "$llamaBaseUrl/models" -ApiKey $BackendKey
        if (-not $existingModels) {
            throw "llama.cpp listener on port $LlamaPort did not pass its authenticated model check; no process was stopped."
        }
        $existingContextSize = Get-LoadedModelContextSize -Models $existingModels -ModelName $ServedAlias
        $runtimeMatches = Test-RequestedLlamaRuntime `
            -CommandLine $existingCommandLine `
            -RequestedBatchSize $BatchSize `
            -RequestedUBatchSize $UBatchSize `
            -RequestedModelPath $ModelPath `
            -RequestedSkipChatParsing ([bool]$SkipChatParsing) `
            -RequestedBinDir $LlamaBinDir
        if ($existingContextSize -ne $ContextSize -or -not $runtimeMatches) {
            throw "Running llama.cpp does not match the requested alias, context, or runtime. Stop it through its owner or use a free port; no process was stopped."
        }
        Write-Ok "llama.cpp already healthy at $llamaBaseUrl (model: $ServedAlias, context: $existingContextSize)"
        $SkipLlamaStart = $true
    }

    if (-not $SkipLlamaStart) {
    if ($LlamaBinDir -match '(?i)cuda' -and -not (Test-NvidiaHealth)) {
        throw 'NVIDIA driver health probe failed or timed out. Reset the GPU/reboot before launching llama.cpp.'
    }
    $llamaArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $resolvedLlamaScript,
        "-Port", $LlamaPort,
        "-Profile", $Profile,
        "-ContextSize", $ContextSize,
        "-Alias", $ServedAlias
    )

    if ($ModelPath) { $llamaArgs += @("-ModelPath", $ModelPath) }
    if ($LlamaBinDir) { $llamaArgs += @("-BinDir", $LlamaBinDir) }
    if ($BatchSize -gt 0) { $llamaArgs += @("-BatchSize", $BatchSize) }
    if ($UBatchSize -gt 0) { $llamaArgs += @("-UBatchSize", $UBatchSize) }
    if ($Metrics)   { $llamaArgs += "-Metrics" }
    if ($SkipChatParsing) { $llamaArgs += "-SkipChatParsing" }

    if (Test-PortListening -Port $LlamaPort) {
        throw "llama.cpp port $LlamaPort became occupied before launch; no process was stopped."
    }
    Write-Host "  Running: powershell $($llamaArgs -join ' ')" -ForegroundColor DarkGray
    Invoke-PowerShellScript -ArgumentList $llamaArgs

    # Wait for llama.cpp to be ready
    Write-Host "  Waiting for llama.cpp to initialize..." -ForegroundColor DarkGray
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (-not (Get-LlamaServerCommandLine)) { continue }
        $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models" -ApiKey $BackendKey
        $modelNames = @()
        if ($models.data) { $modelNames += @($models.data | ForEach-Object { $_.id }) }
        if ($models.models) { $modelNames += @($models.models | ForEach-Object { $_.id; $_.name; $_.model }) }
        $loadedContextSize = Get-LoadedModelContextSize -Models $models -ModelName $ServedAlias
        if ($models -and $modelNames -contains $ServedAlias -and $loadedContextSize -eq $ContextSize) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        Write-Fail "llama.cpp did not become ready within 60s"
        throw "llama.cpp failed to start served alias '$ServedAlias' at requested context $ContextSize."
    }

    Write-Ok "llama.cpp ready at $llamaBaseUrl (model: $ServedAlias, context: $ContextSize)"
    }
} else {
    Write-Step "Phase 1: Skipping llama.cpp startup (user provided)"

    $existingCommandLine = Get-LlamaServerCommandLine
    if (-not $existingCommandLine) {
        throw "No verified llama.cpp listener on port $LlamaPort for -SkipLlamaStart."
    }
    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models" -ApiKey $BackendKey
    if (-not $models) {
        throw "llama.cpp is not responding at $llamaBaseUrl. Start it first or remove -SkipLlamaStart."
    }

    $modelNames = @()
    if ($models.data) { $modelNames += @($models.data | ForEach-Object { $_.id }) }
    if ($models.models) { $modelNames += @($models.models | ForEach-Object { $_.id; $_.name; $_.model }) }
    $hasModel = $modelNames | Where-Object { $_ -eq $ServedAlias }
    if (-not $hasModel) {
        throw "Served alias '$ServedAlias' not found in llama.cpp. Available: $($models.data.id -join ', ')"
    } else {
        $loadedContextSize = Get-LoadedModelContextSize -Models $models -ModelName $ServedAlias
        $runtimeMatches = Test-RequestedLlamaRuntime `
            -CommandLine (Get-LlamaServerCommandLine) `
            -RequestedBatchSize $BatchSize `
            -RequestedUBatchSize $UBatchSize `
            -RequestedModelPath $ModelPath `
            -RequestedSkipChatParsing ([bool]$SkipChatParsing) `
            -RequestedBinDir $LlamaBinDir
        if ($loadedContextSize -ne $ContextSize -or -not $runtimeMatches) {
            throw "Running llama.cpp does not match the requested context or runtime options."
        }
        Write-Ok "llama.cpp confirmed at $llamaBaseUrl"
    }
}

if ($SkipCoherenceCheck) {
    Write-Warn 'Skipping the bounded chat coherence gate by request.'
} else {
    Write-Step 'Checking bounded, coherent chat generation'
    Write-Warn 'This gate performs two real model generations and will briefly raise GPU utilization.'
    & (Join-Path $PSScriptRoot 'Test-WatsonChatHealth.ps1') -BaseUrl $llamaBaseUrl -Model $ServedAlias -ApiKey $BackendKey | Out-Host
}

# ─── Phase 2: LiteLLM Proxy ─────────────────────────────────────
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"

if ($RouteHermesThroughHeadroom -and -not $headroomEnabled) {
    throw "-RouteHermesThroughHeadroom requires Headroom. Remove -NoHeadroom."
}

Write-Step "Phase 2: Starting LiteLLM proxy"

if (Test-PortListening -Port $LiteLLMPort) {
    Confirm-ExistingLiteLLM -Port $LiteLLMPort -ExpectedExecutable $litellmExecutable `
        -BaseUrl $litellmBaseUrl -ApiKey $litellmMasterKey -ModelName $Model -Replace:$ReplaceLiteLLM
    Write-Ok "LiteLLM already running at $litellmBaseUrl"
} else {
    $proxyScript = $litellmRuntimeScript

    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "LiteLLM startup script not found: $proxyScript"
    }

    if (Test-PortListening -Port $LiteLLMPort) {
        throw "LiteLLM port $LiteLLMPort became occupied before launch; no process was stopped."
    }
    Write-Host "  Running LiteLLM on port $LiteLLMPort..." -ForegroundColor DarkGray
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $proxyScript,
        "-LlamaCppBaseUrl", $llamaBaseUrl,
        "-ListenHost", "0.0.0.0",
        "-ListenPort", $LiteLLMPort,
        "-BackendKey", $BackendKey,
        "-UpstreamModel", $ServedAlias,
        "-ModelName", $Model,
        "-LiteLLMExecutable", $litellmExecutable
    ) | Out-Null

    # LiteLLM imports and validates its backend before serving model traffic.
    $proxyReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (-not (Test-LiteLLMListener -Port $LiteLLMPort -ExpectedExecutable $litellmExecutable)) { continue }
        $proxyModels = Test-JsonEndpoint -Uri "$litellmBaseUrl/models" -ApiKey $litellmMasterKey
        if (@($proxyModels.data.id) -contains $Model) {
            $proxyReady = $true
            break
        }
    }
    if (-not $proxyReady) {
        throw "LiteLLM did not become ready within 60s. Check the LiteLLM process and logs."
    }
    Write-Ok "LiteLLM ready at $litellmBaseUrl"
}

# ─── Phase 3: webchat2api (Oracle) ──────────────────────────────

if ($headroomEnabled) {
    Write-Step "Phase 2b: Starting Headroom context proxy"
    $headroomScript = Join-Path $PSScriptRoot "Start-HeadroomHermes.ps1"
    if ($ForceHeadroomCompression) {
        & $headroomScript -Port $HeadroomPort -ProtectRecent 0 -ForceKompress
    } else {
        & $headroomScript -Port $HeadroomPort
    }

    if ($RouteHermesThroughHeadroom) {
        $routeScript = Join-Path $PSScriptRoot "Set-HermesHeadroomRoute.ps1"
        & $routeScript -Enable -Port $HeadroomPort
        Write-Ok "Hermes now routes through Headroom on port $HeadroomPort"
    } else {
        Write-Ok "Headroom is running but Hermes routing remains unchanged"
    }
}

if (-not $SkipHermesSync) {
    Write-Step "Phase 2c: Synchronizing Hermes model metadata"
    $syncScript = Join-Path $PSScriptRoot "Sync-HermesQwen38Metadata.ps1"
    if (-not (Test-Path -LiteralPath $syncScript)) {
        throw "Hermes metadata synchronizer not found: $syncScript"
    }
    $wslRoutes = & wsl.exe -d Ubuntu -- ip -j -4 route show default
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the Windows host gateway from WSL.' }
    $wslHostAddress = ($wslRoutes | ConvertFrom-Json | Select-Object -First 1).gateway
    $parsedAddress = $null
    if (-not [Net.IPAddress]::TryParse([string]$wslHostAddress, [ref]$parsedAddress)) {
        throw 'WSL did not report a usable Windows host gateway address.'
    }
    $hermesBaseUrl = if ($headroomEnabled) {
        "http://${wslHostAddress}:$HeadroomPort/v1"
    }
    else {
        "http://${wslHostAddress}:$LiteLLMPort/v1"
    }
    $fallbackBaseUrl = "http://${wslHostAddress}:$LiteLLMPort/v1"
    & $syncScript -LlamaBaseUrl $llamaBaseUrl -ServedAlias $ServedAlias -CanonicalModel $Model `
        -HermesBaseUrl $hermesBaseUrl -FallbackBaseUrl $fallbackBaseUrl
    Write-Ok "Hermes now uses the live llama.cpp context and qwen3.8 fallback"

    # The gateway runs inside WSL. A Windows-local health check is not enough:
    # verify the actual WSL-to-host path that Hermes will use before declaring
    # the stack reachable.
    $wslHealthUrl = "http://${wslHostAddress}:$HeadroomPort/health"
    & wsl.exe -d Ubuntu -- bash -lc "curl -fsS --max-time 5 '$wslHealthUrl' >/dev/null"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL cannot reach Headroom at $wslHealthUrl; Hermes/Discord would time out."
    }
}

if ($oracleEnabled) {
    Write-Step "Phase 3: Starting webchat2api (GPT-5 Oracle)"

    if (Test-PortListening -Port $Webchat2ApiPort) {
        # Check if it's actually webchat2api
        $health = Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"
        if ($health) {
            Write-Ok "webchat2api already running on port $Webchat2ApiPort"
        } else {
            Write-Warn "Port $Webchat2ApiPort occupied by unknown service"
        }
    } else {
        Write-Host "  Launching webchat2api via WSL..." -ForegroundColor DarkGray

        $wslCmd = "cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py &"

        # Run in background via WSL
        Start-Process wsl.exe -ArgumentList "-e", "-c", $wslCmd -WindowStyle Hidden

        # Wait for webchat2api to initialize
        Start-Sleep -Seconds 8
        $oracleHealth = Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"
        if ($oracleHealth) {
            Write-Ok "webchat2api ready on port $Webchat2ApiPort"
        } else {
            Write-Warn "webchat2api may still be starting - check WSL logs if Oracle calls fail"
            $oracleLogHint = "tail -f /home/juanbeck/webchat2api/src/data/logs/*.log"
            Write-Host ("  Tip: Run wsl.exe -d Ubuntu -- bash -lc {0} to monitor" -f $oracleLogHint) -ForegroundColor DarkGray
        }
    }
} else {
    Write-Step "Phase 3: Skipping webchat2api (use -EnableOracle to start it)"
}

# ─── Summary ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "====================================================" -ForegroundColor Magenta
Write-Host "  Stack Status Summary:" -ForegroundColor Green
Write-Host ""

$llamaUp = (Get-LlamaServerCommandLine) -and ($null -ne (Test-JsonEndpoint -Uri "$llamaBaseUrl/models" -ApiKey $BackendKey))
$litellmUp = (Test-LiteLLMListener -Port $LiteLLMPort -ExpectedExecutable $litellmExecutable) -and
    ($null -ne (Test-JsonEndpoint -Uri "$litellmBaseUrl/models" -ApiKey $litellmMasterKey))
$headroomUp = $headroomEnabled -and ($null -ne (Test-JsonEndpoint -Uri "http://127.0.0.1:$HeadroomPort/health"))
$oracleUp = $oracleEnabled -and ($null -ne (Test-JsonEndpoint -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models"))

Write-Host "  llama.cpp    : $(if($llamaUp){'[OK] Running'}else{'[FAIL] Not running'}) on port $LlamaPort" `
    -ForegroundColor $(if($llamaUp){'Green'}else{'Red'})
Write-Host "  LiteLLM      : $(if($litellmUp){'[OK] Running'}else{'[FAIL] Not running'}) on port $LiteLLMPort" `
    -ForegroundColor $(if($litellmUp){'Green'}else{'Red'})
if ($headroomEnabled) {
    Write-Host "  Headroom     : $(if($headroomUp){'[OK] Running (memory enabled)'}else{'[FAIL] Not running'}) on port $HeadroomPort" `
        -ForegroundColor $(if($headroomUp){'Green'}else{'Red'})
}
Write-Host "  webchat2api  : $(if($oracleUp){'[OK] Running'}else{'[SKIP] Skipped'}) on port $Webchat2ApiPort" `
    -ForegroundColor $(if($oracleUp){'Green'}else{'Yellow'})

Write-Host ""
Write-Host "  ZeroTier Network: 3b19b3a716937e29" -ForegroundColor DarkGray
$ztIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "10.88.*" })
if ($ztIp) {
    Write-Host "  ZeroTier IP     : $($ztIp.IPAddress)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Endpoints:" -ForegroundColor DarkGray
Write-Host "    Local:  http://127.0.0.1:$LlamaPort/v1   (llama.cpp direct)" -ForegroundColor DarkGray
Write-Host "    Proxy:  http://127.0.0.1:$LiteLLMPort/v1   (LiteLLM OpenAI-compatible)" -ForegroundColor DarkGray
if ($headroomEnabled) {
    Write-Host "    Headroom: http://127.0.0.1:$HeadroomPort/v1  (context optimization + memory)" -ForegroundColor DarkGray
}
if ($oracleEnabled) {
    Write-Host "    Oracle: http://127.0.0.1:$Webchat2ApiPort/v1  (webchat2api GPT-5)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor Magenta
Write-Host ""
if (-not $llamaUp -or -not $litellmUp -or ($headroomEnabled -and -not $headroomUp)) {
    throw "Stack startup incomplete. Verify llama.cpp, LiteLLM, and Headroom health before using Hermes/Discord."
}
if ($host.Name -eq "ConsoleHost" -and -not [Console]::IsInputRedirected) {
    Write-Host "Press any key to exit this summary (services keep running in background)..."
    $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
