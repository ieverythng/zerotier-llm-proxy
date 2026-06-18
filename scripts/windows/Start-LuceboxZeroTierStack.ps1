param(
    [string]$LuceboxRepo = "C:\Users\Admin\PROJECTS\lucebox-hub",
    [string]$DflashServerBin = "",
    [string]$ModelPath = "D:\MODELS\Qwen3.6-27B-Q3_K_M.gguf",
    [string]$DraftPath = "C:\Users\Admin\PROJECTS\lucebox-models\draft\dflash-draft-3.6-q4_k_m.gguf",
    [int]$LlamaPort = 8080,
    [int]$ContextSize = 131072,
    [int]$Budget = 16,
    [string]$CacheTypeK = "tq3_0",
    [string]$CacheTypeV = "tq3_0",
    [int]$KvFlash = 4096,
    [int]$DflashProxyPort = 18080,
    [int]$LiteLLMPort = 4000,
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [int]$Webchat2ApiPort = 9000,
    [switch]$NoOracle,
    [switch]$SkipDflashStart,
    [switch]$NoSpecDecode
)

$ErrorActionPreference = "Stop"

function Repair-ProcessPathEnvironment {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $pathValue) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }

    if ($pathValue) {
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
        [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
    }
}

function Write-Step { param([string]$Message) Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  [FAIL] $Message" -ForegroundColor Red }

function Test-JsonEndpoint {
    param([string]$Uri)
    try { return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5 }
    catch { return $null }
}

function Test-PortListening {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-JsonEndpoint {
    param([string]$Uri, [int]$TimeoutSeconds = 240)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $response = Test-JsonEndpoint -Uri $Uri
        if ($response) { return $response }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Test-ChatCompletion {
    param([string]$BaseUrl, [string]$Model)

    $body = @{
        model = $Model
        messages = @(@{ role = "user"; content = "Reply with exactly: stack-ok" })
        max_tokens = 16
        temperature = 0
        stream = $false
    } | ConvertTo-Json -Depth 6

    try {
        return Invoke-RestMethod `
            -Uri "$($BaseUrl.TrimEnd('/'))/chat/completions" `
            -Method Post `
            -ContentType "application/json" `
            -Headers @{ Authorization = "Bearer local-qwen36" } `
            -Body $body `
            -TimeoutSec 180
    } catch {
        Write-Warn "Chat smoke failed: $($_.Exception.Message)"
        return $null
    }
}

function Join-CmdArguments {
    param([string[]]$Items)

    $quoted = @()
    foreach ($item in $Items) {
        $quoted += '"' + ($item -replace '"', '\"') + '"'
    }
    return $quoted -join " "
}

function Start-HostedService {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )

    $hostScript = Join-Path $LogDir "$Name.host.cmd"
    $combinedLog = Join-Path $LogDir "$Name.combined.log"
    $argString = Join-CmdArguments -Items $ArgumentList
    $script = @"
@echo off
cd /d "$WorkingDirectory"
"$FilePath" $argString > "$combinedLog" 2>&1
"@
    Set-Content -LiteralPath $hostScript -Value $script -Encoding ASCII

    return Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/c", $hostScript) `
        -WindowStyle Hidden `
        -PassThru
}

$ScriptDir = (Resolve-Path $PSScriptRoot).Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$LogDir = Join-Path $RepoRoot "_tmp\lucebox-stack"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LuceboxRepo = (Resolve-Path $LuceboxRepo).Path
if (-not $DflashServerBin) {
    $DflashServerBin = Join-Path $LuceboxRepo "server\build\dflash_server.exe"
}

$dflashBaseUrl = "http://127.0.0.1:$LlamaPort/v1"
$dflashProxyBaseUrl = "http://127.0.0.1:$DflashProxyPort/v1"
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"

Repair-ProcessPathEnvironment

Write-Host ""
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "       Lucebox DFlash + ZeroTier Stack" -ForegroundColor Magenta
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host "  DFlash : $DflashServerBin" -ForegroundColor DarkGray
Write-Host "  Model  : $ModelPath" -ForegroundColor DarkGray
Write-Host "  Draft  : $(if ($NoSpecDecode) { '(disabled)' } else { $DraftPath })" -ForegroundColor DarkGray
Write-Host "  Context: $ContextSize" -ForegroundColor DarkGray
Write-Host "  Logs   : $LogDir" -ForegroundColor DarkGray

if (-not $SkipDflashStart) {
    Write-Step "Starting DFlash"
    if (-not (Test-Path -LiteralPath $DflashServerBin)) { throw "Missing DFlash binary: $DflashServerBin" }
    if (-not (Test-Path -LiteralPath $ModelPath)) { throw "Missing model: $ModelPath" }

    $args = @(
        $ModelPath,
        "--port", "$LlamaPort",
        "--host", "0.0.0.0",
        "--max-ctx", "$ContextSize",
        "--cache-type-k", $CacheTypeK,
        "--cache-type-v", $CacheTypeV,
        "--model-name", "qwen36-turbo-hermes-spec"
    )

    if ($KvFlash -gt 0) {
        $args += @("--kvflash", "$KvFlash")
    }

    if (-not $NoSpecDecode) {
        $args += @("--ddtree", "--ddtree-budget", "$Budget")
        if (Test-Path -LiteralPath $DraftPath) {
            $args += @("--draft", $DraftPath)
        } else {
            Write-Warn "Draft model not found; starting without --draft: $DraftPath"
        }
    }

    Write-Host "  dflash_server $($args -join ' ')" -ForegroundColor DarkGray
    $process = Start-HostedService `
        -Name "dflash" `
        -FilePath $DflashServerBin `
        -ArgumentList $args `
        -WorkingDirectory (Join-Path $LuceboxRepo "server")
    Write-Host "  PID: $($process.Id)" -ForegroundColor DarkGray

    $models = Wait-JsonEndpoint -Uri "$dflashBaseUrl/models" -TimeoutSeconds 300
    if (-not $models) {
        Write-Fail "DFlash did not become ready"
        throw "DFlash startup failed"
    }
    Write-Ok "DFlash ready at $dflashBaseUrl"
} else {
    Write-Step "Using existing DFlash"
    if (-not (Test-JsonEndpoint -Uri "$dflashBaseUrl/models")) {
        throw "DFlash is not responding at $dflashBaseUrl"
    }
    Write-Ok "DFlash confirmed at $dflashBaseUrl"
}

Write-Step "Starting DFlash compatibility proxy"
if (Test-PortListening -Port $DflashProxyPort) {
    Write-Ok "DFlash proxy port $DflashProxyPort is already listening"
} else {
    $proxyScript = Join-Path $ScriptDir "lucebox_dflash_proxy.py"
    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "Missing DFlash proxy script: $proxyScript"
    }

    Start-HostedService `
        -Name "dflash-proxy" `
        -FilePath "python" `
        -ArgumentList @($proxyScript, "--host", "127.0.0.1", "--port", "$DflashProxyPort", "--upstream", "http://127.0.0.1:$LlamaPort") `
        -WorkingDirectory $ScriptDir | Out-Null
    Start-Sleep -Seconds 2
}

if (Test-JsonEndpoint -Uri "$dflashProxyBaseUrl/models") {
    Write-Ok "DFlash proxy ready at $dflashProxyBaseUrl"
} else {
    Write-Warn "DFlash proxy is not responding yet at $dflashProxyBaseUrl"
}

Write-Step "Starting LiteLLM"
if (Test-PortListening -Port $LiteLLMPort) {
    Write-Ok "Port $LiteLLMPort is already listening"
} else {
    $litellmConfig = Resolve-Path (Join-Path $ScriptDir "..\..\config\server\litellm-config.yaml")
    $env:LLAMA_CPP_API_KEY = "local-qwen36"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Start-HostedService `
        -Name "litellm" `
        -FilePath "litellm" `
        -ArgumentList @("--config", $litellmConfig.Path, "--host", "0.0.0.0", "--port", "$LiteLLMPort") `
        -WorkingDirectory $RepoRoot | Out-Null
}

$litellmModels = Wait-JsonEndpoint -Uri "$litellmBaseUrl/models" -TimeoutSeconds 120
if ($litellmModels) {
    Write-Ok "LiteLLM ready at $litellmBaseUrl"
} else {
    Write-Warn "LiteLLM is not responding yet at $litellmBaseUrl"
}

if (-not $NoOracle) {
    Write-Step "Starting webchat2api"
    if (Test-PortListening -Port $Webchat2ApiPort) {
        Write-Ok "webchat2api port $Webchat2ApiPort already listening"
    } else {
        $wslCmd = "cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py"
        Start-Process wsl.exe -ArgumentList @("-e", "sh", "-lc", $wslCmd) -WindowStyle Hidden | Out-Null
        Start-Sleep -Seconds 8
    }
} else {
    Write-Step "Skipping webchat2api"
}

Write-Host ""
Write-Host "Stack Status:" -ForegroundColor Green
Write-Host "  DFlash : $(if (Test-JsonEndpoint -Uri "$dflashBaseUrl/models") { 'running' } else { 'down' }) on $dflashBaseUrl"
Write-Host "  Proxy  : $(if (Test-JsonEndpoint -Uri "http://127.0.0.1:$DflashProxyPort/health") { 'running' } else { 'down' }) on $dflashProxyBaseUrl"
Write-Host "  LiteLLM: $(if (Test-JsonEndpoint -Uri "$litellmBaseUrl/models") { 'running' } else { 'down' }) on $litellmBaseUrl"
if (-not $NoOracle) {
    Write-Host "  Oracle : $(if (Test-PortListening -Port $Webchat2ApiPort) { 'running' } else { 'down' }) on http://127.0.0.1:$Webchat2ApiPort/v1"
}

Write-Step "Running LiteLLM chat smoke"
$chatSmoke = Test-ChatCompletion -BaseUrl $litellmBaseUrl -Model "qwen36-turbo-hermes"
if ($chatSmoke -and $chatSmoke.choices -and $chatSmoke.choices[0].message.content) {
    Write-Ok "LiteLLM chat smoke: $($chatSmoke.choices[0].message.content)"
} else {
    Write-Warn "LiteLLM chat smoke did not return content"
}
