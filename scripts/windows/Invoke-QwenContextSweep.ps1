param(
    [string[]]$ServerContextSizes = @("65536", "98304"),
    [string[]]$PromptContextTokens = @("0", "8192", "32768", "65536"),
    [int]$RestoreContextSize = 65536,
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [string]$Model = "qwen36-turbo-hermes",
    [string]$ApiKey = "local-qwen36",
    [int]$RequestsPerContext = 1,
    [int]$MaxOutputTokens = 64,
    [switch]$IncludeStress128k,
    [switch]$SkipRestore
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$llamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server"
$stopScript = Join-Path $llamaRepo "scripts\stop_llama_server.ps1"
$startScript = Join-Path $PSScriptRoot "Start-Qwen36ZeroTierStack.ps1"
$measureScript = Join-Path $PSScriptRoot "Measure-Qwen36ProxyThroughput.ps1"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $repoRoot "_tmp\bench\context-sweep-$stamp"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Convert-ContextList {
    param([string[]]$Values)

    $parsed = @()
    foreach ($item in $Values) {
        if ($null -eq $item) {
            continue
        }

        foreach ($part in ([string]$item -split ",")) {
            $trimmed = $part.Trim()
            if ($trimmed -eq "") {
                continue
            }

            $parsed += [int]$trimmed
        }
    }

    if ($parsed.Count -eq 0) {
        throw "No context values were provided."
    }

    return $parsed
}

function Get-GpuSnapshot {
    $csv = & nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw --format=csv,noheader,nounits
    $parts = $csv -split "\s*,\s*"
    return [pscustomobject]@{
        gpu_name = $parts[0]
        gpu_memory_total_mib = [int]$parts[1]
        gpu_memory_used_mib = [int]$parts[2]
        gpu_memory_free_mib = [int]$parts[3]
        gpu_utilization_pct = [int]$parts[4]
        gpu_temperature_c = [int]$parts[5]
        gpu_power_w = [double]$parts[6]
    }
}

function Get-LlamaModelMeta {
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:$LlamaPort/v1/models" -TimeoutSec 10
    $modelInfo = $models.data | Where-Object { $_.id -eq $Model } | Select-Object -First 1
    if (-not $modelInfo) {
        throw "Model '$Model' was not returned by llama.cpp on port $LlamaPort."
    }

    return $modelInfo
}

function Stop-Llama {
    if (Test-Path -LiteralPath $stopScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $LlamaPort -Preset q3
    }
}

function Start-Llama {
    param([int]$ContextSize)

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript `
        -LlamaPort $LlamaPort `
        -LiteLLMPort $LiteLLMPort `
        -ContextSize $ContextSize `
        -Metrics
}

$parsedServerContextSizes = @(Convert-ContextList -Values $ServerContextSizes)
$parsedPromptContextTokens = @(Convert-ContextList -Values $PromptContextTokens)

if ($IncludeStress128k -and ($parsedServerContextSizes -notcontains 131072)) {
    $parsedServerContextSizes += 131072
}

if (-not $IncludeStress128k) {
    $parsedServerContextSizes = @($parsedServerContextSizes | Where-Object { $_ -lt 131072 })
}

$summaryRows = @()
try {
    foreach ($serverContextSize in $parsedServerContextSizes) {
        Write-Host "=== context size $serverContextSize ==="
        Stop-Llama
        Start-Llama -ContextSize $serverContextSize

        $meta = Get-LlamaModelMeta
        $gpuBefore = Get-GpuSnapshot
        $promptContexts = @($parsedPromptContextTokens | Where-Object { $null -ne $_ -and $_ -le $serverContextSize })
        if ($promptContexts.Count -eq 0) {
            throw "No prompt context values are <= server context size $serverContextSize."
        }
        $benchCsv = Join-Path $outDir ("throughput-ctx{0}.csv" -f $serverContextSize)

        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $measureScript `
            -BaseUrl "http://127.0.0.1:$LiteLLMPort/v1" `
            -ApiKey $ApiKey `
            -Model $Model `
            -ContextTokens ($promptContexts -join ",") `
            -RequestsPerContext $RequestsPerContext `
            -MaxOutputTokens $MaxOutputTokens `
            -OutCsv $benchCsv

        $gpuAfter = Get-GpuSnapshot
        foreach ($row in (Import-Csv -LiteralPath $benchCsv)) {
            $summaryRows += [pscustomobject]@{
                server_context_size = $serverContextSize
                reported_n_ctx = [int]$meta.meta.n_ctx
                prompt_context_target = [int]$row.context_tokens_target
                elapsed_s = [double]$row.elapsed_s
                completion_tokens = [int]$row.completion_tokens
                completion_tok_s = [double]$row.completion_tok_s
                total_tok_s = [double]$row.total_tok_s
                gpu_used_before_mib = $gpuBefore.gpu_memory_used_mib
                gpu_free_before_mib = $gpuBefore.gpu_memory_free_mib
                gpu_used_after_mib = $gpuAfter.gpu_memory_used_mib
                gpu_free_after_mib = $gpuAfter.gpu_memory_free_mib
                bench_csv = $benchCsv
            }
        }
    }
}
finally {
    if (-not $SkipRestore) {
        Write-Host "Restoring context size $RestoreContextSize"
        Stop-Llama
        Start-Llama -ContextSize $RestoreContextSize
    }
}

$summaryCsv = Join-Path $outDir "summary.csv"
$summaryRows | Export-Csv -NoTypeInformation -Path $summaryCsv
Write-Host "Wrote $summaryCsv"
