param(
    [string]$LlamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server",
    [string]$LlamaScript = "scripts\start_turbo_hermes.ps1",
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [string]$Model = "qwen36-turbo-hermes",
    [string]$BackendKey = "llama.cpp",
    [switch]$SkipLlamaStart
)

$ErrorActionPreference = "Stop"

function Test-JsonEndpoint {
    param([string]$Uri)

    try {
        return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5
    } catch {
        return $null
    }
}

function Assert-PortFreeOrOwned {
    param(
        [int]$Port,
        [string]$ServiceName
    )

    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listeners) {
        $processes = $listeners |
            ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue } |
            Select-Object -ExpandProperty ProcessName -Unique
        Write-Host ("{0} already listening on port {1}: {2}" -f $ServiceName, $Port, ($processes -join ", "))
        return $true
    }

    return $false
}

$llamaBaseUrl = "http://127.0.0.1:$LlamaPort/v1"
$litellmBaseUrl = "http://127.0.0.1:$LiteLLMPort/v1"

$models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
$hasModel = $models -and (($models.data | ForEach-Object { $_.id }) -contains $Model)

if (-not $hasModel -and -not $SkipLlamaStart) {
    $resolvedLlamaRepo = Resolve-Path -LiteralPath $LlamaRepo
    $resolvedLlamaScript = Join-Path $resolvedLlamaRepo $LlamaScript
    if (-not (Test-Path -LiteralPath $resolvedLlamaScript)) {
        throw "llama.cpp startup script not found: $resolvedLlamaScript"
    }

    Write-Host "Starting llama.cpp via $resolvedLlamaScript"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $resolvedLlamaScript -Port $LlamaPort

    $models = Test-JsonEndpoint -Uri "$llamaBaseUrl/models"
    $hasModel = $models -and (($models.data | ForEach-Object { $_.id }) -contains $Model)
}

if (-not $hasModel) {
    throw "llama.cpp is not serving model '$Model' at $llamaBaseUrl/models."
}

Write-Host "llama.cpp ready at $llamaBaseUrl"

if (Assert-PortFreeOrOwned -Port $LiteLLMPort -ServiceName "LiteLLM") {
    $proxyModels = Test-JsonEndpoint -Uri "$litellmBaseUrl/models"
    if ($proxyModels -and (($proxyModels.data | ForEach-Object { $_.id }) -contains $Model)) {
        Write-Host "LiteLLM already ready at $litellmBaseUrl"
        return
    }

    throw "Port $LiteLLMPort is occupied, but LiteLLM did not expose model '$Model'."
}

$proxyScript = Join-Path $PSScriptRoot "Start-Qwen36LiteLLM.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $proxyScript `
    -LlamaCppBaseUrl $llamaBaseUrl `
    -ListenHost "0.0.0.0" `
    -ListenPort $LiteLLMPort `
    -BackendKey $BackendKey
