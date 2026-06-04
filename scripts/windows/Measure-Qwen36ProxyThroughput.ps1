param(
    [string]$BaseUrl = "http://127.0.0.1:4000/v1",
    [string]$ApiKey = "local-qwen36",
    [string]$Model = "qwen36-turbo-hermes",
    [string[]]$ContextTokens = @("0", "8192", "32768", "65536"),
    [int]$RequestsPerContext = 2,
    [int]$MaxOutputTokens = 128,
    [double]$Temperature = 0.2,
    [string]$OutCsv = ""
)

$ErrorActionPreference = "Stop"

function New-SyntheticContext {
    param([int]$ApproxTokens)

    if ($ApproxTokens -le 0) {
        return "Short health check prompt. Reply with a compact operational status summary."
    }

    $targetChars = $ApproxTokens * 4
    $line = "session_fact: qwen36 proxy retains this synthetic operational note for long-context throughput testing. "
    $builder = [System.Text.StringBuilder]::new($targetChars + $line.Length)
    while ($builder.Length -lt $targetChars) {
        [void]$builder.Append($line)
    }

    return $builder.ToString()
}

function Get-CompletionText {
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

$headers = @{}
if ($ApiKey) {
    $headers.Authorization = "Bearer $ApiKey"
}

$parsedContextTokens = @()
foreach ($item in $ContextTokens) {
    if ($null -eq $item) {
        continue
    }

    foreach ($part in ([string]$item -split ",")) {
        $trimmed = $part.Trim()
        if ($trimmed -eq "") {
            continue
        }

        $parsedContextTokens += [int]$trimmed
    }
}

if ($parsedContextTokens.Count -eq 0) {
    throw "No context token values were provided."
}

if (-not $OutCsv) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) "_tmp\bench"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $OutCsv = Join-Path $outDir "qwen36-proxy-throughput-$stamp.csv"
}

$outParent = Split-Path -Parent $OutCsv
if ($outParent) {
    New-Item -ItemType Directory -Force -Path $outParent | Out-Null
}

$rows = @()
foreach ($contextTokenCount in $parsedContextTokens) {
    $context = New-SyntheticContext -ApproxTokens $contextTokenCount
    for ($request = 1; $request -le $RequestsPerContext; $request++) {
        $body = [ordered]@{
            model = $Model
            input = @(
                [ordered]@{
                    role = "system"
                    content = "You are measuring endpoint behavior. Answer briefly and preserve the requested key facts."
                },
                [ordered]@{
                    role = "user"
                    content = "$context`n`nReturn a compact status with observed context class and one retained fact."
                }
            )
            temperature = $Temperature
            max_output_tokens = $MaxOutputTokens
        } | ConvertTo-Json -Depth 8

        $stopwatch = [Diagnostics.Stopwatch]::StartNew()
        $response = Invoke-RestMethod `
            -Uri ("{0}/responses" -f $BaseUrl.TrimEnd("/")) `
            -Method Post `
            -Headers $headers `
            -ContentType "application/json" `
            -Body $body `
            -TimeoutSec 900
        $stopwatch.Stop()

        $completionTokens = 0
        $promptTokens = 0
        if ($response.usage) {
            if ($null -ne $response.usage.output_tokens) { $completionTokens = [int]$response.usage.output_tokens }
            if ($null -ne $response.usage.input_tokens) { $promptTokens = [int]$response.usage.input_tokens }
        }

        $text = Get-CompletionText -Response $response
        if ($completionTokens -le 0) {
            $completionTokens = [Math]::Max(1, [int]($text.Length / 4))
        }

        $seconds = [Math]::Max($stopwatch.Elapsed.TotalSeconds, 0.001)
        $row = [pscustomobject]@{
            context_tokens_target = $contextTokenCount
            request = $request
            elapsed_s = [Math]::Round($seconds, 3)
            prompt_tokens = $promptTokens
            completion_tokens = $completionTokens
            completion_tok_s = [Math]::Round($completionTokens / $seconds, 3)
            total_tok_s = [Math]::Round(($completionTokens + $promptTokens) / $seconds, 3)
            output_preview = $text.Substring(0, [Math]::Min(120, $text.Length))
        }
        $rows += $row

        Write-Host ("context={0} request={1} elapsed={2:n2}s completion_tok_s={3:n2}" -f $contextTokenCount, $request, $seconds, ($completionTokens / $seconds))
    }
}

$rows | Export-Csv -NoTypeInformation -Path $OutCsv
Write-Host "Wrote $OutCsv"
