param(
    [string]$BaseUrl = "http://127.0.0.1:8080",
    [int]$PromptTokens = 42000,
    [int]$TokenTolerance = 256,
    [int]$MaxOutputTokens = 32,
    [double]$Temperature = 0.0,
    [string]$Variant = "current",
    [string]$CorpusPath = "",
    [string]$OutCsv = "",
    [int]$RequestTimeoutSec = 900,
    [switch]$Stream,
    [switch]$IgnoreEos,
    [ValidateRange(100, 5000)]
    [int]$GpuSampleIntervalMs = 250
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function ConvertTo-JsonStringLiteral {
    param([AllowNull()][string]$Text)

    if ($null -eq $Text) { return "null" }
    $escaped = $Text.Replace("\", "\\")
    $escaped = $escaped.Replace('"', '\"')
    $escaped = $escaped.Replace("`r", "\r")
    $escaped = $escaped.Replace("`n", "\n")
    $escaped = $escaped.Replace("`t", "\t")
    return '"' + $escaped + '"'
}

function Write-Phase {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] $Message"
}

function Invoke-Tokenize {
    param([string]$Text)

    $body = '{"content":' + (ConvertTo-JsonStringLiteral -Text $Text) + ',"add_special":false}'

    $response = Invoke-RestMethod `
        -Uri ("{0}/tokenize" -f $BaseUrl.TrimEnd("/")) `
        -Method Post `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 120

    return @($response.tokens).Count
}

function New-SeedPrompt {
    param([int]$TargetChars)

    $line = "session_fact: watson fixed corpus prefill measurement uses a deterministic tokenized prompt so baseline and challenger runs compare the same text. "
    $builder = [System.Text.StringBuilder]::new($TargetChars + 4096)
    [void]$builder.AppendLine("BEGIN_WATSON_FIXED_CORPUS")
    while ($builder.Length -lt $TargetChars) {
        [void]$builder.Append($line)
    }
    [void]$builder.AppendLine("")
    [void]$builder.AppendLine("END_WATSON_FIXED_CORPUS")
    [void]$builder.Append("Return one compact sentence containing BEGIN_WATSON_FIXED_CORPUS and END_WATSON_FIXED_CORPUS.")
    return $builder.ToString()
}

function New-CalibratedPrompt {
    param([int]$TargetTokens, [int]$Tolerance)

    $targetChars = [Math]::Max(512, $TargetTokens * 4)
    $bestPrompt = ""
    $bestTokenCount = 0
    $bestDistance = [int]::MaxValue

    for ($i = 0; $i -lt 12; $i++) {
        $prompt = New-SeedPrompt -TargetChars $targetChars
        $tokenCount = Invoke-Tokenize -Text $prompt
        $distance = [Math]::Abs($tokenCount - $TargetTokens)

        if ($distance -lt $bestDistance -and $tokenCount -le $TargetTokens) {
            $bestPrompt = $prompt
            $bestTokenCount = $tokenCount
            $bestDistance = $distance
        }

        if ($distance -le $Tolerance -and $tokenCount -le $TargetTokens) {
            return [pscustomobject]@{
                prompt = $prompt
                token_count = $tokenCount
            }
        }

        $ratio = $TargetTokens / [Math]::Max($tokenCount, 1)
        $targetChars = [int]([Math]::Max(512, $targetChars * $ratio * 0.985))
    }

    if (-not $bestPrompt) {
        throw "Could not calibrate a prompt at or below $TargetTokens tokens."
    }

    return [pscustomobject]@{
        prompt = $bestPrompt
        token_count = $bestTokenCount
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

function Start-GpuSampler {
    param([int]$IntervalMs)

    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi) {
        return $null
    }

    $samplePath = Join-Path $env:TEMP ("watson-gpu-samples-{0}.csv" -f [guid]::NewGuid().ToString("N"))
    $errorPath = "$samplePath.err"
    $process = Start-Process `
        -FilePath $nvidiaSmi.Source `
        -ArgumentList @(
            "--query-gpu=memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
            "--loop-ms=$IntervalMs"
        ) `
        -RedirectStandardOutput $samplePath `
        -RedirectStandardError $errorPath `
        -WindowStyle Hidden `
        -PassThru

    return [pscustomobject]@{
        process = $process
        sample_path = $samplePath
        error_path = $errorPath
    }
}

function Stop-GpuSampler {
    param($Job)

    if (-not $Job) {
        return [pscustomobject]@{
            sample_count = 0
            minimum_free_mib = $null
            maximum_utilization_pct = $null
        }
    }

    $process = $Job.process
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        [void]$process.WaitForExit(5000)
    }

    $samples = @()
    if (Test-Path -LiteralPath $Job.sample_path) {
        foreach ($line in Get-Content -LiteralPath $Job.sample_path) {
            if ($line -match '^\s*(\d+)\s*,\s*(\d+)\s*$') {
                $samples += [pscustomobject]@{
                    gpu_free_mib = [int]$Matches[1]
                    gpu_utilization_pct = [int]$Matches[2]
                }
            }
        }
        Remove-Item -LiteralPath $Job.sample_path -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $Job.error_path) {
        Remove-Item -LiteralPath $Job.error_path -Force -ErrorAction SilentlyContinue
    }

    $freeSamples = @($samples | ForEach-Object { $_.gpu_free_mib })
    $utilizationSamples = @($samples | ForEach-Object { $_.gpu_utilization_pct })

    return [pscustomobject]@{
        sample_count = $samples.Count
        minimum_free_mib = if ($freeSamples.Count) { ($freeSamples | Measure-Object -Minimum).Minimum } else { $null }
        maximum_utilization_pct = if ($utilizationSamples.Count) { ($utilizationSamples | Measure-Object -Maximum).Maximum } else { $null }
    }
}

function Invoke-StreamingCompletion {
    param(
        [string]$Uri,
        [string]$RequestBody,
        [int]$TimeoutSec,
        [Diagnostics.Stopwatch]$Elapsed
    )

    $client = [Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, $Uri)
    $request.Content = [Net.Http.StringContent]::new($RequestBody, [Text.Encoding]::UTF8, "application/json")
    $httpResponse = $null
    $reader = $null
    try {
        $httpResponse = $client.SendAsync(
            $request,
            [Net.Http.HttpCompletionOption]::ResponseHeadersRead
        ).GetAwaiter().GetResult()
        $responseHeadersMs = $Elapsed.Elapsed.TotalMilliseconds
        [void]$httpResponse.EnsureSuccessStatusCode()
        $stream = $httpResponse.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $reader = [IO.StreamReader]::new($stream)
        $content = [Text.StringBuilder]::new()
        $lastEvent = $null
        $firstTokenMs = $null

        while (-not $reader.EndOfStream) {
            $line = $reader.ReadLineAsync().GetAwaiter().GetResult()
            if (-not $line -or -not $line.StartsWith("data: ")) { continue }
            $data = $line.Substring(6)
            if ($data -eq "[DONE]") { break }
            $event = $data | ConvertFrom-Json
            $lastEvent = $event
            $chunk = [string]$event.content
            if ($chunk) {
                if ($null -eq $firstTokenMs) {
                    $firstTokenMs = $Elapsed.Elapsed.TotalMilliseconds
                }
                [void]$content.Append($chunk)
            }
        }

        if (-not $lastEvent) {
            throw "Streaming completion returned no data events."
        }

        return [pscustomobject]@{
            response = $lastEvent
            content = $content.ToString()
            response_headers_ms = $responseHeadersMs
            first_token_ms = $firstTokenMs
        }
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($httpResponse) { $httpResponse.Dispose() }
        $request.Dispose()
        $client.Dispose()
    }
}

if (-not $OutCsv) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) "_tmp\bench\autoresearch-prefill-loop"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $OutCsv = Join-Path $outDir ("direct-prefill-{0}-{1}.csv" -f $Variant, $stamp)
}

$outParent = Split-Path -Parent $OutCsv
if ($outParent) {
    New-Item -ItemType Directory -Force -Path $outParent | Out-Null
}

if ($CorpusPath -and (Test-Path -LiteralPath $CorpusPath)) {
    Write-Phase "Reading fixed corpus: $CorpusPath"
    $prompt = Get-Content -LiteralPath $CorpusPath -Raw
    Write-Phase "Tokenizing fixed corpus"
    $tokenCount = Invoke-Tokenize -Text $prompt
}
else {
    Write-Phase "Generating calibrated corpus near $PromptTokens tokens"
    $calibrated = New-CalibratedPrompt -TargetTokens $PromptTokens -Tolerance $TokenTolerance
    $prompt = [string]$calibrated.prompt
    $tokenCount = [int]$calibrated.token_count
    if ($CorpusPath) {
        $corpusParent = Split-Path -Parent $CorpusPath
        if ($corpusParent) { New-Item -ItemType Directory -Force -Path $corpusParent | Out-Null }
        Set-Content -LiteralPath $CorpusPath -Value $prompt -Encoding UTF8
    }
}

$body = '{' +
    '"prompt":' + (ConvertTo-JsonStringLiteral -Text $prompt) + ',' +
    '"n_predict":' + $MaxOutputTokens + ',' +
    '"temperature":' + $Temperature.ToString([System.Globalization.CultureInfo]::InvariantCulture) + ',' +
    '"cache_prompt":false,' +
    '"ignore_eos":' + $(if ($IgnoreEos) { 'true' } else { 'false' }) + ',' +
    '"stream":' + $(if ($Stream) { 'true' } else { 'false' }) +
    '}'

$gpuBefore = Get-GpuFreeMiB
$gpuSampler = Start-GpuSampler -IntervalMs $GpuSampleIntervalMs
# Give the background sampler time to capture an idle point before submission.
if ($gpuSampler) { Start-Sleep -Milliseconds ([Math]::Min(500, $GpuSampleIntervalMs * 2)) }
$stopwatch = [Diagnostics.Stopwatch]::StartNew()
$response = $null
$content = ""
$responseHeadersMs = $null
$firstTokenMs = $null
$requestError = ""
try {
    Write-Phase "Submitting /completion: variant=$Variant tokenized=$tokenCount stream=$([bool]$Stream) timeout=${RequestTimeoutSec}s"
    $completionUri = "{0}/completion" -f $BaseUrl.TrimEnd("/")
    if ($Stream) {
        $streamed = Invoke-StreamingCompletion `
            -Uri $completionUri `
            -RequestBody $body `
            -TimeoutSec $RequestTimeoutSec `
            -Elapsed $stopwatch
        $response = $streamed.response
        $content = [string]$streamed.content
        $responseHeadersMs = $streamed.response_headers_ms
        $firstTokenMs = $streamed.first_token_ms
    }
    else {
        $response = Invoke-RestMethod `
            -Uri $completionUri `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body $body `
            -TimeoutSec $RequestTimeoutSec
        $content = [string]$response.content
    }
}
catch {
    $requestError = $_.Exception.Message
}
finally {
    $stopwatch.Stop()
    $gpuSamples = Stop-GpuSampler -Job $gpuSampler
    $gpuAfter = Get-GpuFreeMiB
}

$timings = if ($response) { $response.timings } else { $null }
$row = [pscustomobject]@{
    variant = $Variant
    status = if ($requestError) { "failed" } else { "ok" }
    streaming = [bool]$Stream
    ignore_eos = [bool]$IgnoreEos
    prompt_tokens_target = $PromptTokens
    prompt_tokens_tokenized = $tokenCount
    prompt_chars = $prompt.Length
    elapsed_s = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
    response_headers_ms = if ($null -ne $responseHeadersMs) { [Math]::Round([double]$responseHeadersMs, 3) } else { "" }
    first_token_ms = if ($null -ne $firstTokenMs) { [Math]::Round([double]$firstTokenMs, 3) } else { "" }
    cache_n = if ($timings -and $null -ne $timings.cache_n) { [int]$timings.cache_n } else { "" }
    prompt_n = if ($timings -and $null -ne $timings.prompt_n) { [int]$timings.prompt_n } else { "" }
    prompt_ms = if ($timings -and $null -ne $timings.prompt_ms) { [Math]::Round([double]$timings.prompt_ms, 3) } else { "" }
    prompt_per_second = if ($timings -and $null -ne $timings.prompt_per_second) { [Math]::Round([double]$timings.prompt_per_second, 3) } else { "" }
    predicted_ms = if ($timings -and $null -ne $timings.predicted_ms) { [Math]::Round([double]$timings.predicted_ms, 3) } else { "" }
    predicted_per_second = if ($timings -and $null -ne $timings.predicted_per_second) { [Math]::Round([double]$timings.predicted_per_second, 3) } else { "" }
    draft_n = if ($timings -and $null -ne $timings.draft_n) { [int]$timings.draft_n } else { "" }
    draft_n_accepted = if ($timings -and $null -ne $timings.draft_n_accepted) { [int]$timings.draft_n_accepted } else { "" }
    draft_acceptance_pct = if ($timings -and [int]$timings.draft_n -gt 0) {
        [Math]::Round(100.0 * [int]$timings.draft_n_accepted / [int]$timings.draft_n, 3)
    } else { "" }
    tokens_cached = if ($response -and $null -ne $response.tokens_cached) { [int]$response.tokens_cached } else { "" }
    tokens_evaluated = if ($response -and $null -ne $response.tokens_evaluated) { [int]$response.tokens_evaluated } else { "" }
    gpu_free_before_mib = $gpuBefore
    gpu_free_min_mib = if ($null -ne $gpuSamples.minimum_free_mib) { [int]$gpuSamples.minimum_free_mib } else { "" }
    gpu_free_after_mib = $gpuAfter
    gpu_max_utilization_pct = if ($null -ne $gpuSamples.maximum_utilization_pct) { [int]$gpuSamples.maximum_utilization_pct } else { "" }
    gpu_sample_count = [int]$gpuSamples.sample_count
    gpu_sample_interval_ms = $GpuSampleIntervalMs
    corpus_path = $CorpusPath
    output_preview = $content.Substring(0, [Math]::Min(160, $content.Length))
    error = $requestError
}

$row | Export-Csv -NoTypeInformation -Path $OutCsv
$row | Format-List
Write-Host "Wrote $OutCsv"
if ($row.status -eq "failed") {
    exit 1
}
