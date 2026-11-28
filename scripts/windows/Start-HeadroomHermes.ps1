[CmdletBinding()]
param(
    [int]$Port = 8787,
    [string]$ListenHost = "0.0.0.0",
    [string]$LiteLLMUpstream = "http://127.0.0.1:4000/v1",
    [int]$ProtectRecent = 12,
    [int]$MinTokens = 1000,
    [switch]$ForceKompress,
    [switch]$NoMemory
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot ".runtime\headroom"
$headroomExe = Join-Path $runtimeRoot ".venv\Scripts\headroom.exe"
$runtimeSuffix = if ($Port -eq 8787) { '' } else { ".$Port" }
$logFile = Join-Path $runtimeRoot "headroom$runtimeSuffix.log"
$workerFile = Join-Path $runtimeRoot "headroom$runtimeSuffix.worker.ps1"

if (-not (Test-Path -LiteralPath $headroomExe)) {
    throw "Headroom is not installed. Run .\scripts\windows\Install-HeadroomHermes.ps1 first."
}

# Headroom 0.40 requires authentication when binding beyond loopback. PowerShell
# processes started before a user environment-variable change do not inherit it,
# so explicitly import the user-scoped token before launching the hidden worker.
if ([string]::IsNullOrWhiteSpace($env:HEADROOM_PROXY_TOKEN)) {
    $env:HEADROOM_PROXY_TOKEN = [Environment]::GetEnvironmentVariable("HEADROOM_PROXY_TOKEN", "User")
}
if ([string]::IsNullOrWhiteSpace($env:HEADROOM_PROXY_TOKEN)) {
    throw "HEADROOM_PROXY_TOKEN is required for Headroom's 0.0.0.0 listener. Set a random token in the Windows user environment before starting the stack."
}

function Get-HeadroomUpstreamHeaders {
    param([Uri]$Upstream, [string]$MasterKey, [string]$ConfiguredHeaders)

    # Inherited headers have no destination metadata and may be from a prior local run.
    if (-not $Upstream.IsLoopback) { return $null }
    if ([string]::IsNullOrWhiteSpace($MasterKey)) { return $ConfiguredHeaders }

    $upstreamHeaders = [ordered]@{}
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredHeaders)) {
        try { $parsedHeaders = ConvertFrom-Json -InputObject $ConfiguredHeaders -ErrorAction Stop }
        catch { throw 'OPENAI_TARGET_API_HEADERS must be a JSON object of string header values.' }
        if ($null -eq $parsedHeaders -or $parsedHeaders -isnot [PSCustomObject]) {
            throw 'OPENAI_TARGET_API_HEADERS must be a JSON object of string header values.'
        }
        foreach ($property in $parsedHeaders.PSObject.Properties) {
            if ($property.Value -isnot [string]) {
                throw 'OPENAI_TARGET_API_HEADERS must be a JSON object of string header values.'
            }
            if ($property.Name -ne 'Authorization') {
                $upstreamHeaders[$property.Name] = $property.Value
            }
        }
    }
    $upstreamHeaders['Authorization'] = "Bearer $MasterKey"
    return ConvertTo-Json -InputObject $upstreamHeaders -Compress
}

function Set-HeadroomWorkerUpstream {
    param($WorkerEnvironment, [Uri]$Upstream, [string]$Headers)

    $WorkerEnvironment.Remove('LITELLM_MASTER_KEY')
    $WorkerEnvironment.Remove('OPENAI_TARGET_API_HEADERS')
    $WorkerEnvironment['OPENAI_TARGET_API_URL'] = $Upstream.AbsoluteUri
    if ($Upstream.IsLoopback -and -not [string]::IsNullOrWhiteSpace($Headers)) {
        $WorkerEnvironment['OPENAI_TARGET_API_HEADERS'] = $Headers
    }
}

$upstreamUri = $null
if (-not [Uri]::TryCreate($LiteLLMUpstream, [UriKind]::Absolute, [ref]$upstreamUri) -or
    $upstreamUri.Scheme -notin @('http', 'https')) {
    throw 'LiteLLMUpstream must be an absolute HTTP(S) URL.'
}
$litellmMasterKey = ''
if ($upstreamUri.IsLoopback) {
    $litellmMasterKey = $env:LITELLM_MASTER_KEY
    if ([string]::IsNullOrWhiteSpace($litellmMasterKey)) {
        $litellmMasterKey = [Environment]::GetEnvironmentVariable('LITELLM_MASTER_KEY', 'User')
    }
}
$workerHeaders = Get-HeadroomUpstreamHeaders -Upstream $upstreamUri -MasterKey $litellmMasterKey -ConfiguredHeaders $env:OPENAI_TARGET_API_HEADERS

function Test-HeadroomListenerReady {
    param([int]$Port, [string]$ExpectedExecutable)

    $owners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object LocalPort -eq $Port | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -eq 0) { return $false }

    # Python entry-point launchers can leave the listening socket in a child process.
    $verified = $false
    if ($owners.Count -eq 1 -and $owners[0] -gt 0) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($owners[0])" -ErrorAction SilentlyContinue
        for ($depth = 0; $process -and $depth -lt 4; $depth++) {
            if ($process.ExecutablePath -eq $ExpectedExecutable) { $verified = $true; break }
            if (-not $process.ParentProcessId) { break }
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
            if (-not $parent.CreationDate -or -not $process.CreationDate -or $parent.CreationDate -gt $process.CreationDate) { break }
            $process = $parent
        }
    }
    if (-not $verified) { throw "Headroom port $Port is occupied by an unverified listener; no process was stopped." }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3 -MaximumRedirection 0
    } catch {
        $health = $null
    }
    if ($health.status -ne 'healthy') {
        throw "Headroom listener on port $Port did not pass its health check. Check authentication and service health; no process was stopped."
    }
    return $true
}

if (Test-HeadroomListenerReady -Port $Port -ExpectedExecutable $headroomExe) {
    if (-not $upstreamUri.IsLoopback) {
        throw "Headroom already listens on port $Port; the requested remote upstream was not applied. Stop it through its owner or use a free port."
    }
    Write-Host "Headroom already listens on port $Port; existing configuration is unchanged." -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$worker = @'
Set-Location "__REPO_ROOT__"
$env:HEADROOM_TELEMETRY = "off"
$env:HEADROOM_ROLLOUT_CHANNEL = "canary"
# v0.37 shares sticky tool definitions across Chat and Responses even though
# their function schemas differ. Per-request injection preserves both formats
# and keeps memory enabled without replaying the other API's schema.
$env:HEADROOM_TOOL_INJECTION_STICKY = "disabled"
$env:HEADROOM_EXCLUDE_TOOLS = "read_file,headroom_retrieve"
$env:HEADROOM_MIN_TOKENS = "__MIN_TOKENS__"
$env:HEADROOM_PROTECT_RECENT = "__PROTECT_RECENT__"
$env:HEADROOM_FORCE_KOMPRESS = "__FORCE_KOMPRESS__"
# Hermes/Discord and local Codex share one intentional user-scoped memory store.
# Explicit global mode also keeps Responses memory-save and memory-search on the
# same backend while Headroom's per-project continuation seam is repaired upstream.
& "__HEADROOM_EXE__" proxy --host __LISTEN_HOST__ --port __PORT__ --mode token --intercept-tool-results --no-subscription-tracking --no-telemetry __MEMORY_ARGS__ *> "__LOG_FILE__"
'@
$memoryArguments = if ($NoMemory) { '' } else { '--memory --memory-storage global' }
$worker = $worker.Replace("__REPO_ROOT__", $repoRoot).
    Replace("__MIN_TOKENS__", $MinTokens).
    Replace("__PROTECT_RECENT__", $ProtectRecent).
    Replace("__FORCE_KOMPRESS__", $(if ($ForceKompress) { "1" } else { "0" })).
    Replace("__HEADROOM_EXE__", $headroomExe).
    Replace("__LISTEN_HOST__", $ListenHost).
    Replace("__MEMORY_ARGS__", $memoryArguments).
    Replace("__PORT__", $Port).
    Replace("__LOG_FILE__", $logFile)
Set-Content -LiteralPath $workerFile -Value $worker -Encoding ASCII
$workerStart = New-Object System.Diagnostics.ProcessStartInfo
$workerStart.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$workerStart.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$workerFile`""
$workerStart.WorkingDirectory = $repoRoot
$workerStart.UseShellExecute = $false
$workerStart.CreateNoWindow = $true
Set-HeadroomWorkerUpstream -WorkerEnvironment $workerStart.EnvironmentVariables -Upstream $upstreamUri -Headers $workerHeaders
$workerProcess = [System.Diagnostics.Process]::Start($workerStart)
$workerProcess.Dispose()

for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        if (Test-HeadroomListenerReady -Port $Port -ExpectedExecutable $headroomExe) {
            Write-Host "Headroom ready: http://127.0.0.1:$Port/v1" -ForegroundColor Green
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 1
}

throw "Headroom did not become healthy. See $logFile"
