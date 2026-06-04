param(
    [int]$ContextSize = 65536,
    [string[]]$CacheTypeK = @("q8_0"),
    [string[]]$CacheTypeV = @("turbo2", "turbo3", "turbo4", "q8_0"),
    [int[]]$BatchSize = @(512),
    [int[]]$UBatchSize = @(256),
    [int]$Parallel = 1,
    [string[]]$PromptContextTokens = @("0", "8192"),
    [int]$RequestsPerContext = 1,
    [int]$MaxOutputTokens = 64,
    [int]$LlamaPort = 8080,
    [int]$LiteLLMPort = 4000,
    [string]$Model = "qwen36-turbo-hermes",
    [string]$ApiKey = "local-qwen36",
    [int]$RestoreContextSize = 65536,
    [switch]$SkipRestore
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$llamaRepo = "C:\Users\Admin\PROJECTS\llama-cpp-server"
$sourceBinDir = "C:\Users\Admin\PROJECTS\llama-cpp-turboquant\build-cuda-faall\bin"
$bridgeBinDir = Join-Path $llamaRepo "_tmp\llama-bin-wslbridge"
$prepareScript = Join-Path $llamaRepo "scripts\prepare_wsl_bridge_bin.ps1"
$startBgScript = Join-Path $llamaRepo "scripts\start_llama_server_bg.ps1"
$stopScript = Join-Path $llamaRepo "scripts\stop_llama_server.ps1"
$restoreScript = Join-Path $PSScriptRoot "Start-Qwen36ZeroTierStack.ps1"
$measureScript = Join-Path $PSScriptRoot "Measure-Qwen36ProxyThroughput.ps1"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $repoRoot "_tmp\bench\kv-sweep-$stamp"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

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

function Stop-Llama {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $LlamaPort -Preset q3
}

function Start-TunedLlama {
    param(
        [string]$K,
        [string]$V,
        [int]$Batch,
        [int]$UBatch
    )

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $prepareScript `
        -SourceBinDir $sourceBinDir `
        -TargetBinDir $bridgeBinDir

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startBgScript `
        -Preset q3 `
        -ModelSeries qwen36 `
        -BindHost "0.0.0.0" `
        -Port $LlamaPort `
        -ContextSize $ContextSize `
        -GpuLayers "99" `
        -Fit on `
        -FlashAttention on `
        -Reasoning off `
        -CacheTypeK $K `
        -CacheTypeV $V `
        -Temperature 0.2 `
        -TopP 0.8 `
        -TopK 20 `
        -MinP 0.0 `
        -PresencePenalty 1.5 `
        -RepeatPenalty 1.0 `
        -Parallel $Parallel `
        -BatchSize $Batch `
        -UBatchSize $UBatch `
        -Alias $Model `
        -BinDir $bridgeBinDir `
        -Metrics
}

function Restore-Llama {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $restoreScript `
        -LlamaPort $LlamaPort `
        -LiteLLMPort $LiteLLMPort `
        -ContextSize $RestoreContextSize `
        -Metrics
}

$summaryRows = @()
try {
    foreach ($k in $CacheTypeK) {
        foreach ($v in $CacheTypeV) {
            foreach ($batch in $BatchSize) {
                foreach ($ubatch in $UBatchSize) {
                    $label = "ctx{0}-k{1}-v{2}-b{3}-ub{4}" -f $ContextSize, $k, $v, $batch, $ubatch
                    Write-Host "=== $label ==="
                    Stop-Llama
                    Start-TunedLlama -K $k -V $v -Batch $batch -UBatch $ubatch

                    $gpuBefore = Get-GpuSnapshot
                    $benchCsv = Join-Path $outDir ("throughput-{0}.csv" -f $label)
                    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $measureScript `
                        -BaseUrl "http://127.0.0.1:$LiteLLMPort/v1" `
                        -ApiKey $ApiKey `
                        -Model $Model `
                        -ContextTokens ($PromptContextTokens -join ",") `
                        -RequestsPerContext $RequestsPerContext `
                        -MaxOutputTokens $MaxOutputTokens `
                        -OutCsv $benchCsv

                    $gpuAfter = Get-GpuSnapshot
                    foreach ($row in (Import-Csv -LiteralPath $benchCsv)) {
                        $summaryRows += [pscustomobject]@{
                            context_size = $ContextSize
                            cache_type_k = $k
                            cache_type_v = $v
                            batch_size = $batch
                            ubatch_size = $ubatch
                            parallel = $Parallel
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
        }
    }
}
finally {
    if (-not $SkipRestore) {
        Write-Host "Restoring context size $RestoreContextSize"
        Stop-Llama
        Restore-Llama
    }
}

$summaryCsv = Join-Path $outDir "summary.csv"
$summaryRows | Export-Csv -NoTypeInformation -Path $summaryCsv
Write-Host "Wrote $summaryCsv"

