param(
    [string]$BenchRoot = "",
    [string]$OutCsv = "",
    [int]$Top = 20,
    [switch]$IncludeFailures
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $BenchRoot) {
    $BenchRoot = Join-Path $repoRoot "_tmp\bench"
}

if (-not (Test-Path -LiteralPath $BenchRoot)) {
    throw "Benchmark root not found: $BenchRoot"
}

$summaryFiles = @(Get-ChildItem -LiteralPath $BenchRoot -Recurse -Filter "summary.csv" -File)
if ($summaryFiles.Count -eq 0) {
    throw "No summary.csv files found under $BenchRoot"
}

function Convert-ToNullableDouble {
    param($Value)

    if ($null -eq $Value -or [string]$Value -eq "") {
        return $null
    }

    return [double]$Value
}

function Convert-ToNullableInt {
    param($Value)

    if ($null -eq $Value -or [string]$Value -eq "") {
        return $null
    }

    return [int]$Value
}

$rows = @()
foreach ($file in $summaryFiles) {
    $kind = if ($file.Directory.Name -like "kv-sweep-*") { "kv" } elseif ($file.Directory.Name -like "context-sweep-*") { "context" } else { "unknown" }

    foreach ($row in (Import-Csv -LiteralPath $file.FullName)) {
        $status = if ($row.PSObject.Properties.Name -contains "status" -and $row.status) { $row.status } else { "ok" }
        if (-not $IncludeFailures -and $status -ne "ok") {
            continue
        }

        $serverContext = $null
        if ($row.PSObject.Properties.Name -contains "server_context_size") {
            $serverContext = Convert-ToNullableInt $row.server_context_size
        }
        elseif ($row.PSObject.Properties.Name -contains "context_size") {
            $serverContext = Convert-ToNullableInt $row.context_size
        }

        $promptContext = Convert-ToNullableInt $row.prompt_context_target
        $completionTokS = Convert-ToNullableDouble $row.completion_tok_s
        $totalTokS = Convert-ToNullableDouble $row.total_tok_s
        $gpuFreeBefore = Convert-ToNullableInt $row.gpu_free_before_mib
        $gpuFreeAfter = Convert-ToNullableInt $row.gpu_free_after_mib
        if (-not $IncludeFailures -and $null -eq $completionTokS) {
            continue
        }

        $rows += [pscustomobject]@{
            kind = $kind
            status = $status
            server_context_size = $serverContext
            prompt_context_target = $promptContext
            cache_type_k = if ($row.PSObject.Properties.Name -contains "cache_type_k") { $row.cache_type_k } else { "" }
            cache_type_v = if ($row.PSObject.Properties.Name -contains "cache_type_v") { $row.cache_type_v } else { "" }
            batch_size = if ($row.PSObject.Properties.Name -contains "batch_size") { $row.batch_size } else { "" }
            ubatch_size = if ($row.PSObject.Properties.Name -contains "ubatch_size") { $row.ubatch_size } else { "" }
            elapsed_s = Convert-ToNullableDouble $row.elapsed_s
            completion_tok_s = $completionTokS
            total_tok_s = $totalTokS
            gpu_free_before_mib = $gpuFreeBefore
            gpu_free_after_mib = $gpuFreeAfter
            error = if ($row.PSObject.Properties.Name -contains "error") { $row.error } else { "" }
            summary_csv = $file.FullName
        }
    }
}

$ranked = $rows |
    Sort-Object `
        @{ Expression = { if ($null -eq $_.prompt_context_target) { -1 } else { $_.prompt_context_target } }; Descending = $true },
        @{ Expression = { if ($null -eq $_.completion_tok_s) { -1 } else { $_.completion_tok_s } }; Descending = $true },
        @{ Expression = { if ($null -eq $_.gpu_free_before_mib) { -1 } else { $_.gpu_free_before_mib } }; Descending = $true }

if ($OutCsv) {
    $outParent = Split-Path -Parent $OutCsv
    if ($outParent) {
        New-Item -ItemType Directory -Force -Path $outParent | Out-Null
    }

    $ranked | Export-Csv -NoTypeInformation -Path $OutCsv
    Write-Host "Wrote $OutCsv"
}

$ranked |
    Select-Object -First $Top kind,status,server_context_size,prompt_context_target,cache_type_k,cache_type_v,batch_size,ubatch_size,elapsed_s,completion_tok_s,total_tok_s,gpu_free_before_mib,gpu_free_after_mib,error |
    Format-Table -AutoSize
