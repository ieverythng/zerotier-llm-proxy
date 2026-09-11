param(
    [string]$BaseUrl = "http://127.0.0.1:8080/v1",
    [string]$Model = "qwen3.8",
    [string]$Variant = "current",
    [string]$OutJson = "",
    [switch]$DisableThinking,
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"

function Get-WordCount {
    param([string]$Text)

    return @($Text -split "\s+" | Where-Object { $_ }).Count
}

function Invoke-SuiteCase {
    param(
        [string]$Name,
        [string]$System,
        [string]$Prompt,
        [int]$MaxTokens,
        [scriptblock]$Validator
    )

    $body = [ordered]@{
        model = $Model
        messages = @(
            [ordered]@{ role = "system"; content = $System },
            [ordered]@{ role = "user"; content = $Prompt }
        )
        temperature = 0
        max_tokens = $MaxTokens
    }
    if ($DisableThinking) {
        $body.chat_template_kwargs = @{ enable_thinking = $false }
    }
    $body = $body | ConvertTo-Json -Depth 10

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-RestMethod `
            -Uri ("{0}/chat/completions" -f $BaseUrl.TrimEnd("/")) `
            -Method Post `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec $TimeoutSec
        $stopwatch.Stop()
        $text = [string]$response.choices[0].message.content
        $validation = & $Validator $text

        return [pscustomobject]@{
            case = $Name
            passed = [bool]$validation.passed
            reason = [string]$validation.reason
            elapsed_s = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
            word_count = Get-WordCount -Text $text
            output = $text
            error = ""
        }
    }
    catch {
        $stopwatch.Stop()
        return [pscustomobject]@{
            case = $Name
            passed = $false
            reason = "request or validator failed"
            elapsed_s = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
            word_count = 0
            output = ""
            error = $_.Exception.Message
        }
    }
}

if (-not $OutJson) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) "_tmp\bench\qualitative"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $OutJson = Join-Path $outDir ("qualitative-{0}-{1}.json" -f $Variant, $stamp)
}

$outParent = Split-Path -Parent $OutJson
if ($outParent) { New-Item -ItemType Directory -Force -Path $outParent | Out-Null }

$rows = @()
$rows += Invoke-SuiteCase `
    -Name "context_explanation" `
    -System "Answer directly and accurately. Obey the requested word limit." `
    -Prompt "In at most 90 words, explain why a model trained for 262k context can still be served with only a 65,536-token context window. Mention the runtime allocation and KV-cache memory tradeoff." `
    -MaxTokens 160 `
    -Validator {
        param($Text)
        $words = Get-WordCount -Text $Text
        $hasRuntime = $Text -match "(?i)runtime|server|configured|allocation"
        $hasKv = $Text -match "(?i)KV|key.value"
        [pscustomobject]@{
            passed = ($words -le 90 -and $hasRuntime -and $hasKv)
            reason = "words=$words runtime=$hasRuntime kv=$hasKv"
        }
    }

$rows += Invoke-SuiteCase `
    -Name "code_diagnosis_json" `
    -System "Return strict JSON only, with no markdown or commentary." `
    -Prompt 'Diagnose this JavaScript loop: `for (let i = 0; i <= items.length; i++) total += items[i].price;`. Return exactly two string keys: "bug" and "fix".' `
    -MaxTokens 128 `
    -Validator {
        param($Text)
        try {
            $parsed = $Text | ConvertFrom-Json -ErrorAction Stop
            $mentionsBound = ([string]$parsed.bug -match "(?i)bound|length|off.by.one|undefined") -and
                ([string]$parsed.fix -match "i\s*<\s*items\.length")
            [pscustomobject]@{ passed = $mentionsBound; reason = "strict JSON and corrected bound=$mentionsBound" }
        }
        catch {
            [pscustomobject]@{ passed = $false; reason = "invalid JSON" }
        }
    }

$rows += Invoke-SuiteCase `
    -Name "constraint_plan_json" `
    -System "Return strict JSON only, with no markdown or commentary." `
    -Prompt 'Tasks have these dependencies: A before B; A before C; both B and C before D. Return one valid order as exactly {"order":["A","B","C","D"]} or {"order":["A","C","B","D"]}.' `
    -MaxTokens 96 `
    -Validator {
        param($Text)
        try {
            $parsed = $Text | ConvertFrom-Json -ErrorAction Stop
            $order = @($parsed.order) -join ","
            $valid = $order -in @("A,B,C,D", "A,C,B,D")
            [pscustomobject]@{ passed = $valid; reason = "order=$order" }
        }
        catch {
            [pscustomobject]@{ passed = $false; reason = "invalid JSON" }
        }
    }

$rows += Invoke-SuiteCase `
    -Name "uncertainty_restraint" `
    -System "Do not invent unavailable facts. State what evidence you need." `
    -Prompt "What exact commit introduced the private nao repository's Universal Agentic Harness, and what files changed? You have not been given repository access in this conversation." `
    -MaxTokens 160 `
    -Validator {
        param($Text)
        $admitsUnknown = $Text -match "(?i)cannot|can't|do not have|don't have|not been given|need access"
        $requestsEvidence = $Text -match "(?i)git|commit|history|log|repository|inspect"
        [pscustomobject]@{
            passed = ($admitsUnknown -and $requestsEvidence)
            reason = "admits_unknown=$admitsUnknown requests_evidence=$requestsEvidence"
        }
    }

$rows += Invoke-SuiteCase `
    -Name "discord_response_style" `
    -System "Write like a concise technical collaborator in Discord. No heading and no preamble." `
    -Prompt "A user says their 100k-token prompt feels frozen on a 16 GB GPU. Reply in at most three bullets and 110 words. Explain the likely bottleneck and give the first two measurements you would take." `
    -MaxTokens 180 `
    -Validator {
        param($Text)
        $words = Get-WordCount -Text $Text
        $bulletCount = @($Text -split "`n" | Where-Object { $_ -match "^\s*[-*]" }).Count
        $hasPrefill = $Text -match "(?i)prefill|prompt processing"
        $hasMeasurements = $Text -match "(?i)TTFT|tok|token|VRAM|memory|throughput"
        [pscustomobject]@{
            passed = ($words -le 110 -and $bulletCount -le 3 -and $hasPrefill -and $hasMeasurements)
            reason = "words=$words bullets=$bulletCount prefill=$hasPrefill measurements=$hasMeasurements"
        }
    }

$summary = [pscustomobject]@{
    variant = $Variant
    base_url = $BaseUrl
    model = $Model
    generated_at = (Get-Date).ToString("o")
    passed = @($rows | Where-Object passed).Count
    total = $rows.Count
    total_elapsed_s = [Math]::Round((($rows | Measure-Object elapsed_s -Sum).Sum), 3)
    rows = $rows
}

$summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutJson -Encoding UTF8
$rows | Select-Object case, passed, reason, elapsed_s, word_count | Format-Table -AutoSize
Write-Host "Wrote $OutJson"

if ($summary.passed -ne $summary.total) {
    exit 1
}
