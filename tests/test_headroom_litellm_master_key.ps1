[CmdletBinding()]
param(
    [int]$LiteLLMPort = 18401,
    [int]$HeadroomPort = 18788
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$liteLLMScript = Join-Path $repoRoot 'scripts\windows\Start-Qwen36LiteLLM.ps1'
$headroomScript = Join-Path $repoRoot 'scripts\windows\Start-HeadroomHermes.ps1'
$litellmFixture = Join-Path $PSScriptRoot 'fixtures\litellm-bonsai-auth.yaml'
$litellmExe = Join-Path $repoRoot '.runtime\litellm\.venv\Scripts\litellm.exe'
$windowsPowerShell = (Get-Command powershell.exe -CommandType Application -ErrorAction Stop).Source
$logDir = Join-Path $repoRoot '.runtime\auth-integration-test'
$liveLlamaUrl = 'http://127.0.0.1:8080/v1'
$liteBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"
$headroomBaseUrl = "http://127.0.0.1:$HeadroomPort/v1"
$backendKey = 'llama.cpp'
$processNames = @('LITELLM_MASTER_KEY', 'LLAMA_CPP_API_KEY', 'HEADROOM_PROXY_TOKEN', 'OPENAI_TARGET_API_HEADERS', 'HEADROOM_BEACON')
$oldEnvironment = @{}
$liteLauncher = $null
$headroomLauncher = $null

function Get-RandomHex {
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) }
    finally { $rng.Dispose() }
    return -join ($buffer | ForEach-Object { $_.ToString('x2') })
}

function Get-FreeVramMiB {
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction Stop
    $value = & $nvidiaSmi.Source --query-gpu=memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1
    if ($value -notmatch '^\s*\d+\s*$') { throw 'Could not sample free VRAM.' }
    return [int]$value.Trim()
}

function Get-AvailableRamBytes {
    $operatingSystem = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    return [double]$operatingSystem.FreePhysicalMemory * 1KB
}

function Assert-ResourceReserve {
    param([string]$Phase)
    $freeVram = Get-FreeVramMiB
    $availableRam = Get-AvailableRamBytes
    if ($freeVram -lt 2048 -or $availableRam -lt 2GB) {
        throw "Safety floor breached during $Phase (VRAM free: $freeVram MiB; RAM available: $([math]::Round($availableRam / 1GB, 2)) GiB)."
    }
}

function Assert-PortFree {
    param([int]$Port)
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Test port $Port is already in use; no process was stopped."
    }
}

function Stop-TestListener {
    param(
        [int]$Port,
        [string]$ExpectedCommandPattern
    )
    $owners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($owner in $owners) {
        if (-not $owner -or $owner -eq 0) { continue }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction SilentlyContinue
        if (-not $process -or $process.CommandLine -notmatch $ExpectedCommandPattern) {
            throw "Refusing to stop an unrecognized process that owns test port $Port (PID $owner)."
        }
        & taskkill.exe /PID ([int]$owner) /T /F *> $null
    }
}

function Wait-ForModels {
    param(
        [string]$Uri,
        [hashtable]$Headers,
        [int]$TimeoutSeconds = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try { return Invoke-RestMethod -Uri $Uri -Headers $Headers -TimeoutSec 3 }
        catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $deadline)
    throw "Model catalog did not become ready at $Uri."
}

foreach ($name in $processNames) {
    $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    foreach ($port in @($LiteLLMPort, $HeadroomPort)) { Assert-PortFree -Port $port }
    Assert-ResourceReserve -Phase 'preflight'

    foreach ($path in @($liteLLMScript, $headroomScript, $litellmFixture, $litellmExe)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required test file is missing: $path" }
    }

    $liveModels = Invoke-RestMethod -Uri "$liveLlamaUrl/models" -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
    $liveIds = @($liveModels.data | ForEach-Object { [string]$_.id })
    if ($liveIds -notcontains 'bonsai27b' -or ($liveIds | Where-Object { $_ -match '(?i)qwen' })) {
        throw "The live backend is not Bonsai-only: $($liveIds -join ', ')."
    }
    $slots = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/slots' -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
    if (@($slots | Where-Object { $_.is_processing }).Count -gt 0) { throw 'The Bonsai slot is busy; refusing the auth canary.' }

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $env:LITELLM_MASTER_KEY = 'sk-' + (Get-RandomHex)
    $env:LLAMA_CPP_API_KEY = $backendKey
    $env:HEADROOM_PROXY_TOKEN = Get-RandomHex
    $env:HEADROOM_BEACON = 'off'
    Remove-Item Env:OPENAI_TARGET_API_HEADERS -ErrorAction SilentlyContinue

    $liteArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$liteLLMScript`"",
        '-ConfigPath', "`"$litellmFixture`"", '-UseStaticConfig',
        '-LlamaCppBaseUrl', $liveLlamaUrl,
        '-ListenHost', '127.0.0.1', '-ListenPort', [string]$LiteLLMPort,
        '-BackendKey', $backendKey, '-UpstreamModel', 'bonsai27b', '-ModelName', 'bonsai27b',
        '-LiteLLMExecutable', "`"$litellmExe`""
    ) -join ' '
    $liteLauncher = Start-Process -FilePath $windowsPowerShell -WindowStyle Hidden -PassThru `
        -ArgumentList $liteArguments `
        -RedirectStandardOutput (Join-Path $logDir 'litellm.stdout.log') `
        -RedirectStandardError (Join-Path $logDir 'litellm.stderr.log')

    $masterHeaders = @{ Authorization = "Bearer $env:LITELLM_MASTER_KEY" }
    $candidateModels = Wait-ForModels -Uri "$liteBaseUrl/models" -Headers $masterHeaders
    $candidateIds = @($candidateModels.data | ForEach-Object { [string]$_.id })
    if ($candidateIds -notcontains 'bonsai27b' -or ($candidateIds | Where-Object { $_ -match '(?i)qwen' })) {
        throw "The isolated LiteLLM catalog is not Bonsai-only: $($candidateIds -join ', ')."
    }
    Assert-ResourceReserve -Phase 'LiteLLM candidate startup'

    $headroomArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$headroomScript`"",
        '-Port', [string]$HeadroomPort, '-ListenHost', '127.0.0.1',
        '-LiteLLMUpstream', $liteBaseUrl, '-NoMemory'
    ) -join ' '
    $headroomLauncher = Start-Process -FilePath $windowsPowerShell -WindowStyle Hidden -PassThru `
        -ArgumentList $headroomArguments `
        -RedirectStandardOutput (Join-Path $logDir 'headroom.stdout.log') `
        -RedirectStandardError (Join-Path $logDir 'headroom.stderr.log')

    $healthUri = "http://127.0.0.1:$HeadroomPort/health"
    $healthDeadline = (Get-Date).AddSeconds(45)
    $health = $null
    do {
        try { $health = Invoke-RestMethod -Uri $healthUri -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500 }
        if ($health) { break }
        $headroomLauncher.Refresh()
        if ($headroomLauncher.HasExited -and $headroomLauncher.ExitCode -ne 0) {
            throw "Headroom launcher exited ($($headroomLauncher.ExitCode)); inspect $logDir."
        }
    } while (-not $health -and (Get-Date) -lt $healthDeadline)
    if (-not $health -or $health.status -ne 'healthy') { throw "Isolated Headroom did not become healthy; inspect $logDir." }
    Assert-ResourceReserve -Phase 'Headroom candidate startup'

    $headroomHeaders = @{
        Authorization = "Bearer $env:LITELLM_MASTER_KEY"
        'X-Headroom-Proxy-Token' = $env:HEADROOM_PROXY_TOKEN
        'X-Headroom-Project-Id' = 'watson-global'
        'X-Headroom-User-Id' = 'juanbeck'
    }
    $routedModels = Invoke-RestMethod -Uri "$headroomBaseUrl/models" -Headers $headroomHeaders -TimeoutSec 10
    $routedIds = @($routedModels.data | ForEach-Object { [string]$_.id })
    if ($routedIds -notcontains 'bonsai27b' -or ($routedIds | Where-Object { $_ -match '(?i)qwen' })) {
        throw "Headroom did not expose the expected Bonsai-only catalog: $($routedIds -join ', ')."
    }

    $body = @{
        model = 'bonsai27b'
        messages = @(@{ role = 'user'; content = 'Reply exactly with AUTH_ROUTE_OK and nothing else.' })
        max_tokens = 10
        stream = $true
    } | ConvertTo-Json -Depth 8 -Compress
    $stream = Invoke-WebRequest -Uri "$headroomBaseUrl/chat/completions" -Method Post -Headers $headroomHeaders `
        -ContentType 'application/json' -Body $body -TimeoutSec 45 -UseBasicParsing
    if ($stream.StatusCode -ne 200 -or $stream.Content -notmatch 'data:\s*\[DONE\]' -or $stream.Content -notmatch 'choices') {
        throw 'Authenticated Headroom to LiteLLM stream did not return a choice and [DONE].'
    }
    Assert-ResourceReserve -Phase 'authenticated SSE canary'

    $endSlots = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/slots' -Headers @{ Authorization = "Bearer $backendKey" } -TimeoutSec 5
    if (@($endSlots | Where-Object { $_.is_processing }).Count -gt 0) { throw 'Bonsai did not return to an idle slot after the canary.' }
    Write-Host 'PASS: caller bearer auth reached the isolated LiteLLM models passthrough, configured loopback auth covered chat, and Bonsai SSE ended with [DONE]. No production service or user-scoped secret was changed.' -ForegroundColor Green
}
finally {
    try { Stop-TestListener -Port $HeadroomPort -ExpectedCommandPattern '(?i)headroom\.exe' } catch { Write-Warning $_ }
    try { Stop-TestListener -Port $LiteLLMPort -ExpectedCommandPattern '(?i)litellm\.exe' } catch { Write-Warning $_ }
    foreach ($launcher in @($headroomLauncher, $liteLauncher)) {
        if ($launcher -and -not $launcher.HasExited) { Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue }
    }
    foreach ($name in $processNames) {
        if ($null -eq $oldEnvironment[$name]) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath "Env:$name" -Value $oldEnvironment[$name] }
    }
}
