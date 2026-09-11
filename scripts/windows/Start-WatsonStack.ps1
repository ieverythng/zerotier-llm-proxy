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

# ─── Color helpers ──────────────────────────────────────────────
function Write-Step { param([string]$Msg); Write-Host ("`n[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg); Write-Host ("  [OK] {0}" -f $Msg) -ForegroundColor Green }
function Write-Warn { param([string]$Msg); Write-Host ("  [WARN] {0}" -f $Msg) -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg); Write-Host ("  [FAIL] {0}" -f $Msg) -ForegroundColor Red }

# ─── Health check helper ────────────────────────────────────────
function Test-JsonEndpoint {
    param([string]$Uri)
    try { return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5 }
    catch { return $null }
}

function Test-PortListening {
    param([int]$Port)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return ($conn -ne $null)
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

function Get-ListenerProcessIds {
    param([int]$Port)
    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            Where-Object { $_ -and $_ -ne 0 }
    )
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if (-not $ProcessId) { return }
    try { & taskkill.exe /PID $ProcessId /T /F *> $null } catch { }
    Start-Sleep -Milliseconds 500
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Remove-UnhealthyListener {
    param([int]$Port, [string]$Name)
    $owners = Get-ListenerProcessIds -Port $Port
    if (-not $owners) { return $true }
    foreach ($owner in $owners) {
        Write-Warn "$Name has an unhealthy listener on port $Port (pid $owner); stopping it before launch."
        Stop-ProcessTree -ProcessId ([int]$owner)
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-PortListening -Port $Port)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
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
    $listener = Get-NetTCPConnection -State Listen -LocalPort $LlamaPort -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return '' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($process) {
        return [string]$process.CommandLine
    }
    return ""
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

    $existingModels = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    $existingNames = @()
    if ($existingModels.data) { $existingNames += @($existingModels.data | ForEach-Object { $_.id }) }
    if ($existingModels.models) { $existingNames += @($existingModels.models | ForEach-Object { $_.id; $_.name; $_.model }) }
    $existingContextSize = Get-LoadedModelContextSize -Models $existingModels -ModelName $ServedAlias
    $existingCommandLine = Get-LlamaServerCommandLine
    $runtimeMatches = Test-RequestedLlamaRuntime `
        -CommandLine $existingCommandLine `
        -RequestedBatchSize $BatchSize `
        -RequestedUBatchSize $UBatchSize `
        -RequestedModelPath $ModelPath `
        -RequestedSkipChatParsing ([bool]$SkipChatParsing) `
        -RequestedBinDir $LlamaBinDir
    if ($existingModels -and $existingNames -contains $ServedAlias -and $existingContextSize -eq $ContextSize -and $runtimeMatches) {
        Write-Ok "llama.cpp already healthy at $llamaBaseUrl (model: $ServedAlias, context: $existingContextSize)"
        $SkipLlamaStart = $true
    } elseif ($existingModels) {
        Write-Warn "llama.cpp is healthy but does not match the requested model, context, or runtime options. Reloading."
        $stopScript = Join-Path $resolvedLlamaRepo "scripts\stop_llama_server.ps1"
        if (-not (Test-Path -LiteralPath $stopScript)) {
            throw "Cannot reload llama.cpp: stop script not found: $stopScript"
        }
        Invoke-PowerShellScript -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stopScript, '-Port', $LlamaPort)
        Start-Sleep -Seconds 2
    } elseif (Test-PortListening -Port $LlamaPort) {
        if (-not (Remove-UnhealthyListener -Port $LlamaPort -Name 'llama.cpp')) {
            throw "llama.cpp port $LlamaPort is occupied by an unhealthy listener that could not be stopped. Reboot or reset the GPU before retrying."
        }
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

    Write-Host "  Running: powershell $($llamaArgs -join ' ')" -ForegroundColor DarkGray
    Invoke-PowerShellScript -ArgumentList $llamaArgs

    # Wait for llama.cpp to be ready
    Write-Host "  Waiting for llama.cpp to initialize..." -ForegroundColor DarkGray
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
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

    # Verify it's already running
    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
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

Write-Step 'Checking bounded, coherent chat generation'
& (Join-Path $PSScriptRoot 'Test-WatsonChatHealth.ps1') -BaseUrl $llamaBaseUrl -Model $ServedAlias | Out-Host

# ─── Phase 2: LiteLLM Proxy ─────────────────────────────────────
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"

if ($RouteHermesThroughHeadroom -and -not $headroomEnabled) {
    throw "-RouteHermesThroughHeadroom requires Headroom. Remove -NoHeadroom."
}

Write-Step "Phase 2: Starting LiteLLM proxy"

if (Test-PortListening -Port $LiteLLMPort) {
    $existingProxyModels = Test-JsonEndpoint -Uri "$litellmBaseUrl/models"
    if (-not $existingProxyModels) {
        if (-not (Remove-UnhealthyListener -Port $LiteLLMPort -Name 'LiteLLM')) {
            throw "LiteLLM port $LiteLLMPort is occupied by an unhealthy listener that could not be stopped."
        }
        $ReplaceLiteLLM = $true
    }
    $canonicalVisible = $existingProxyModels -and (@($existingProxyModels.data | ForEach-Object { $_.id }) -contains $Model)
    if ($existingProxyModels -and -not $canonicalVisible -and -not $ReplaceLiteLLM) {
        # A pre-canonical LiteLLM instance is still healthy, but it cannot
        # serve the Hermes qwen3.8 name. Port 4000 is this stack's dedicated
        # proxy, so replace that instance instead of silently leaving Hermes
        # pointed at a model id it cannot resolve.
        Write-Warn "LiteLLM is healthy but lacks qwen3.8; replacing its old alias-only configuration."
        $ReplaceLiteLLM = $true
    }
    if ($ReplaceLiteLLM) {
        Get-NetTCPConnection -State Listen -LocalPort $LiteLLMPort -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    $proxyModels = if (-not $ReplaceLiteLLM) { $existingProxyModels } else { $null }
    if ($proxyModels) {
        Write-Ok "LiteLLM already running at $litellmBaseUrl"
    } else {
        Write-Warn "Port $LiteLLMPort occupied but not serving LiteLLM - you may need to kill the process."
    }
}
if (-not (Test-PortListening -Port $LiteLLMPort)) {
    $proxyScript = Join-Path $PSScriptRoot "Start-Qwen36LiteLLM.ps1"

    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "LiteLLM startup script not found: $proxyScript"
    }

    Write-Host "  Running LiteLLM on port $LiteLLMPort..." -ForegroundColor DarkGray
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $proxyScript,
        "-LlamaCppBaseUrl", $llamaBaseUrl,
        "-ListenHost", "0.0.0.0",
        "-ListenPort", $LiteLLMPort,
        "-BackendKey", $BackendKey,
        "-UpstreamModel", $ServedAlias,
        "-ModelName", $Model
    ) | Out-Null

    # LiteLLM imports and validates its backend before serving model traffic.
    $proxyReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (Test-JsonEndpoint -Uri "$litellmBaseUrl/models") {
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

$llamaUp = $null -ne (Test-JsonEndpoint -Uri "$llamaBaseUrl/models")
$litellmUp = $null -ne (Test-JsonEndpoint -Uri "$litellmBaseUrl/models")
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
