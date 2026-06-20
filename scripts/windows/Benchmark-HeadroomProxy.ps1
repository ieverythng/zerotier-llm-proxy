[CmdletBinding()]
param(
    [string]$DirectBaseUrl = "http://127.0.0.1:4000/v1",
    [string]$HeadroomBaseUrl = "http://127.0.0.1:8787/v1",
    [string]$Model = "qwen36-turbo-hermes",
    [int]$ToolOutputRepeats = 220,
    [int]$MaxTokens = 48,
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"

function Invoke-ChatBenchmark {
    param([string]$Name, [string]$BaseUrl, [array]$Messages)

    $body = @{
        model = $Model
        messages = $Messages
        max_tokens = $MaxTokens
        temperature = 0
        stream = $false
    } | ConvertTo-Json -Depth 12 -Compress

    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/chat/completions" -Method Post `
        -ContentType "application/json" -Headers @{ Authorization = "Bearer local-qwen36" } `
        -Body $body -TimeoutSec 900
    $timer.Stop()

    $seconds = [Math]::Max($timer.Elapsed.TotalSeconds, 0.001)
    $usage = $response.usage
    [pscustomobject]@{
        case = $Name
        endpoint = $BaseUrl
        seconds = [Math]::Round($seconds, 3)
        prompt_tokens = [int]$usage.prompt_tokens
        completion_tokens = [int]$usage.completion_tokens
        prefill_tok_per_s = [Math]::Round(([int]$usage.prompt_tokens / $seconds), 2)
        decode_tok_per_s = [Math]::Round(([int]$usage.completion_tokens / $seconds), 2)
        response = $response.choices[0].message.content
    }
}

function Get-HeadroomStats {
    param([string]$BaseUrl)
    try { Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/v1'))/stats" -TimeoutSec 10 }
    catch { throw "Headroom stats endpoint is unavailable: $($_.Exception.Message)" }
}

$toolOutputLine = '{"timestamp":"2026-06-20T20:00:00Z","service":"hermes-agent","event":"filesystem_scan","path":"C:/Users/Admin/PROJECTS/zerotier-llm-proxy","result":"success","detail":"Repeated diagnostic content that may be summarized while retaining the final deployment status."}'
$toolOutput = (($toolOutputLine + "`n") * $ToolOutputRepeats) + "FINAL_STATUS: LiteLLM is healthy on port 4000."
$messages = @(
    @{ role = "system"; content = "Answer only with the final deployment status from the tool output." },
    @{ role = "user"; content = "Inspect the diagnostic result and report the final deployment status." },
    @{ role = "assistant"; content = ""; tool_calls = @(@{ id = "call_benchmark"; type = "function"; function = @{ name = "terminal"; arguments = "{}" } }) },
    @{ role = "tool"; tool_call_id = "call_benchmark"; content = $toolOutput }
)

# Keep the tool output outside Headroom's protected recent-turn window.
for ($turn = 1; $turn -le 7; $turn++) {
    $messages += @{ role = "user"; content = "Continue the deployment review. Step $turn is complete." }
    $messages += @{ role = "assistant"; content = "Acknowledged deployment review step $turn." }
}
$messages += @{ role = "user"; content = "What is the final deployment status?" }

$results = @()
$results += Invoke-ChatBenchmark -Name "direct-tool-history" -BaseUrl $DirectBaseUrl -Messages $messages

$statsBefore = Get-HeadroomStats -BaseUrl $HeadroomBaseUrl
$results += Invoke-ChatBenchmark -Name "headroom-tool-history" -BaseUrl $HeadroomBaseUrl -Messages $messages
$statsAfter = Get-HeadroomStats -BaseUrl $HeadroomBaseUrl

$summary = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    model = $Model
    tool_output_characters = $toolOutput.Length
    results = $results
    headroom_stats_before = $statsBefore
    headroom_stats_after = $statsAfter
}

if ($OutFile) {
    $directory = Split-Path -Parent $OutFile
    if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $summary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutFile -Encoding UTF8
}

$results | Format-Table case, seconds, prompt_tokens, completion_tokens, prefill_tok_per_s, decode_tok_per_s -AutoSize
Write-Host "Headroom stats before:" -ForegroundColor Cyan
$statsBefore | ConvertTo-Json -Depth 10
Write-Host "Headroom stats after:" -ForegroundColor Cyan
$statsAfter | ConvertTo-Json -Depth 10
