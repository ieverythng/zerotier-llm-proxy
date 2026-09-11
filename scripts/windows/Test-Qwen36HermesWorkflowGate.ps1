param(
    [string]$BaseUrl = "http://127.0.0.1:4000/v1",
    [string]$HeadroomHealthUrl = "http://127.0.0.1:8787/health",
    [string]$ApiKey = "local-qwen36",
    [string]$Model = "qwen3.8",
    [int]$LongContextTokens = 12000,
    [int]$TimeoutSec = 240,
    [switch]$DisableThinking,
    [string]$OutCsv = "",
    [string]$OutJson = ""
)

$ErrorActionPreference = "Stop"

function New-SyntheticHistory {
    param([int]$ApproxTokens)

    $targetChars = [Math]::Max(1024, $ApproxTokens * 4)
    $line = "discord_history: channel=watson-autoresearch gate=tool-json-long-history sentinel=HERMES_GATE_SENTINEL_71429 retained_action=choose_patch_file. "
    $builder = [System.Text.StringBuilder]::new($targetChars + 4096)
    [void]$builder.AppendLine("BEGIN_DISCORD_HISTORY_GATE")
    while ($builder.Length -lt $targetChars) {
        [void]$builder.Append($line)
    }
    [void]$builder.AppendLine("")
    [void]$builder.AppendLine("END_DISCORD_HISTORY_GATE")
    return $builder.ToString()
}

function Get-ResponseText {
    param($Response)

    if ($Response.output_text) {
        return [string]$Response.output_text
    }

    if ($Response.output) {
        $parts = @()
        foreach ($item in $Response.output) {
            if ($item.content) {
                foreach ($content in $item.content) {
                    if ($content.text) {
                        $parts += [string]$content.text
                    }
                }
            }
        }
        if ($parts.Count -gt 0) {
            return ($parts -join "")
        }
    }

    return ""
}

function Invoke-GateCase {
    param(
        [string]$Name,
        [AllowNull()][object]$InputPayload,
        [int]$MaxOutputTokens,
        [scriptblock]$Validator
    )

    $body = [ordered]@{
        model = $Model
        input = $InputPayload
        temperature = 0
        max_output_tokens = $MaxOutputTokens
    }
    if ($DisableThinking) {
        # LiteLLM forwards provider-specific llama.cpp parameters from extra_body.
        # A top-level chat_template_kwargs value is dropped by the proxy.
        $body.extra_body = @{
            chat_template_kwargs = @{ enable_thinking = $false }
        }
    }
    $body = $body | ConvertTo-Json -Depth 12

    $headers = @{}
    if ($ApiKey) {
        $headers.Authorization = "Bearer $ApiKey"
    }

    $gpuBefore = Get-GpuFreeMiB
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-RestMethod `
            -Uri ("{0}/responses" -f $BaseUrl.TrimEnd("/")) `
            -Method Post `
            -Headers $headers `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec $TimeoutSec
        $stopwatch.Stop()
        $gpuAfter = Get-GpuFreeMiB
    }
    catch {
        $stopwatch.Stop()
        $gpuAfter = Get-GpuFreeMiB
        return [pscustomobject]@{
            case = $Name
            passed = $false
            reason = "request failed: $($_.Exception.Message)"
            elapsed_s = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
            input_tokens = ""
            output_tokens = ""
            cached_tokens = ""
            gpu_free_before_mib = $gpuBefore
            gpu_free_after_mib = $gpuAfter
            output_preview = ""
        }
    }

    $text = Get-ResponseText -Response $response
    $passed = $false
    $reason = ""
    try {
        $result = & $Validator $text $response
        if ($result -is [bool]) {
            $passed = $result
        }
        elseif ($result) {
            $passed = [bool]$result.passed
            $reason = [string]$result.reason
        }
    }
    catch {
        $passed = $false
        $reason = $_.Exception.Message
    }

    if (-not $reason) {
        $reason = if ($passed) { "ok" } else { "validator returned false" }
    }

    $inputTokens = ""
    $outputTokens = ""
    $cachedTokens = ""
    if ($response.usage) {
        if ($null -ne $response.usage.input_tokens) { $inputTokens = [int]$response.usage.input_tokens }
        if ($null -ne $response.usage.output_tokens) { $outputTokens = [int]$response.usage.output_tokens }
        if ($response.usage.input_tokens_details -and $null -ne $response.usage.input_tokens_details.cached_tokens) {
            $cachedTokens = [int]$response.usage.input_tokens_details.cached_tokens
        }
    }

    return [pscustomobject]@{
        case = $Name
        passed = $passed
        reason = $reason
        elapsed_s = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
        input_tokens = $inputTokens
        output_tokens = $outputTokens
        cached_tokens = $cachedTokens
        gpu_free_before_mib = $gpuBefore
        gpu_free_after_mib = $gpuAfter
        output_preview = $text.Substring(0, [Math]::Min(220, $text.Length))
    }
}

function Get-GpuFreeMiB {
    try {
        $line = & nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($line) { return [int]$line.Trim() }
    }
    catch {
    }
    return $null
}

if (-not $OutCsv -or -not $OutJson) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) "_tmp\bench\autoresearch-prefill-loop"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    if (-not $OutCsv) { $OutCsv = Join-Path $outDir "hermes-workflow-gate-$stamp.csv" }
    if (-not $OutJson) { $OutJson = Join-Path $outDir "hermes-workflow-gate-$stamp.json" }
}

$outCsvParent = Split-Path -Parent $OutCsv
if ($outCsvParent) { New-Item -ItemType Directory -Force -Path $outCsvParent | Out-Null }
$outJsonParent = Split-Path -Parent $OutJson
if ($outJsonParent) { New-Item -ItemType Directory -Force -Path $outJsonParent | Out-Null }

$headroomBefore = $null
try {
    $headroomBefore = Invoke-RestMethod -Uri $HeadroomHealthUrl -TimeoutSec 10
}
catch {
    throw "Headroom health failed before gate: $($_.Exception.Message)"
}

$history = New-SyntheticHistory -ApproxTokens $LongContextTokens
$rows = @()

$rows += Invoke-GateCase `
    -Name "exact_response" `
    -MaxOutputTokens 32 `
    -InputPayload "Reply with exactly: HERMES_WORKFLOW_GATE_OK" `
    -Validator {
        param($Text, $Response)
        [pscustomobject]@{
            passed = ($Text.Trim() -eq "HERMES_WORKFLOW_GATE_OK")
            reason = "expected exact marker"
        }
    }

$rows += Invoke-GateCase `
    -Name "tool_json_selection" `
    -MaxOutputTokens 96 `
    -InputPayload @(
        [ordered]@{
            role = "system"
            content = "You are validating Hermes tool selection. Return only strict JSON, no markdown."
        },
        [ordered]@{
            role = "user"
            content = "For a repo file edit, choose the correct tool from [read_file, apply_patch, shell]. Return exactly JSON with keys tool and reason. The tool must be apply_patch."
        }
    ) `
    -Validator {
        param($Text, $Response)
        try {
            $parsed = $Text | ConvertFrom-Json -ErrorAction Stop
            $ok = ($parsed.tool -eq "apply_patch")
            [pscustomobject]@{ passed = $ok; reason = "strict JSON tool=$($parsed.tool)" }
        }
        catch {
            [pscustomobject]@{ passed = $false; reason = "invalid JSON: $($_.Exception.Message)" }
        }
    }

$rows += Invoke-GateCase `
    -Name "long_history_retention" `
    -MaxOutputTokens 96 `
    -InputPayload @(
        [ordered]@{
            role = "system"
            content = "You are validating long Discord context retention. Answer briefly."
        },
        [ordered]@{
            role = "user"
            content = "$history`n`nReturn the sentinel and retained_action exactly as: HERMES_GATE_SENTINEL_71429 choose_patch_file"
        }
    ) `
    -Validator {
        param($Text, $Response)
        $ok = ($Text -match "HERMES_GATE_SENTINEL_71429") -and ($Text -match "choose_patch_file")
        [pscustomobject]@{ passed = $ok; reason = "sentinel/action retention" }
    }

$rows += Invoke-GateCase `
    -Name "continuation_summary" `
    -MaxOutputTokens 128 `
    -InputPayload @(
        [ordered]@{
            role = "system"
            content = "You are validating agent continuation. Return compact JSON only."
        },
        [ordered]@{
            role = "user"
            content = "Prior agent state: benchmark repeated, route gate passed, next gate is Hermes Discord workflow. Return JSON with continue=true and next_gate='hermes_discord_workflow'."
        }
    ) `
    -Validator {
        param($Text, $Response)
        try {
            $parsed = $Text | ConvertFrom-Json -ErrorAction Stop
            $ok = ([bool]$parsed.continue -eq $true) -and ([string]$parsed.next_gate -eq "hermes_discord_workflow")
            [pscustomobject]@{ passed = $ok; reason = "continuation JSON" }
        }
        catch {
            [pscustomobject]@{ passed = $false; reason = "invalid JSON: $($_.Exception.Message)" }
        }
    }

$headroomAfter = Invoke-RestMethod -Uri $HeadroomHealthUrl -TimeoutSec 10

$rows | Export-Csv -NoTypeInformation -Path $OutCsv

$summary = [pscustomobject]@{
    base_url = $BaseUrl
    headroom_health_url = $HeadroomHealthUrl
    model = $Model
    long_context_tokens_target = $LongContextTokens
    all_passed = -not ($rows | Where-Object { -not $_.passed })
    case_count = $rows.Count
    total_elapsed_s = [Math]::Round((($rows | Measure-Object -Property elapsed_s -Sum).Sum), 3)
    headroom_before = $headroomBefore
    headroom_after = $headroomAfter
    rows = $rows
}

$summary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutJson -Encoding UTF8
$rows | Format-Table -AutoSize
Write-Host "Wrote $OutCsv"
Write-Host "Wrote $OutJson"

if (-not $summary.all_passed) {
    exit 1
}

exit 0
