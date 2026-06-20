param(
    [string]$BaseUrl = "http://10.88.140.94:4000/v1",
    [string]$Model = "qwen36-turbo-hermes-spec",
    [int]$LongPromptRepeats = 620,
    [int]$MaxTokens = 64,
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"

function Invoke-TimedJson {
    param(
        [string]$Name,
        [string]$Uri,
        [hashtable]$Body,
        [int]$TimeoutSec = 300
    )

    $jsonBody = $Body | ConvertTo-Json -Depth 12 -Compress
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod `
        -Uri $Uri `
        -Method Post `
        -ContentType "application/json" `
        -Headers @{ Authorization = "Bearer local-qwen36" } `
        -Body $jsonBody `
        -TimeoutSec $TimeoutSec
    $sw.Stop()

    $usage = $response.usage
    $promptTokens = if ($usage.prompt_tokens -ne $null) { [int]$usage.prompt_tokens } else { 0 }
    $completionTokens = if ($usage.completion_tokens -ne $null) { [int]$usage.completion_tokens } else { 0 }
    $seconds = [Math]::Max($sw.Elapsed.TotalSeconds, 0.001)

    $text = ""
    if ($response.choices) {
        $choice = $response.choices[0]
        if ($choice.message -and $choice.message.content) {
            $text = $choice.message.content
        } elseif ($choice.text) {
            $text = $choice.text
        }
    }

    [pscustomobject]@{
        case = $Name
        endpoint = $Uri
        seconds = [Math]::Round($seconds, 3)
        prompt_tokens = $promptTokens
        completion_tokens = $completionTokens
        total_tokens = $promptTokens + $completionTokens
        prompt_tok_per_s = [Math]::Round($promptTokens / $seconds, 2)
        completion_tok_per_s = [Math]::Round($completionTokens / $seconds, 2)
        text = $text
    }
}

$base = $BaseUrl.TrimEnd("/")
$results = @()

$models = Invoke-RestMethod -Uri "$base/models" -Method Get -TimeoutSec 15
$contextLength = $null
foreach ($item in $models.data) {
    if ($item.id -eq $Model) {
        $contextLength = $item.context_length
        if (-not $contextLength) { $contextLength = $item.max_context_length }
        break
    }
}

$results += [pscustomobject]@{
    case = "models"
    endpoint = "$base/models"
    seconds = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    prompt_tok_per_s = 0
    completion_tok_per_s = 0
    text = "model=$Model context_length=$contextLength"
}

$results += Invoke-TimedJson `
    -Name "chat-short" `
    -Uri "$base/chat/completions" `
    -Body @{
        model = $Model
        messages = @(@{ role = "user"; content = "Hey. Reply in one short sentence." })
        max_tokens = $MaxTokens
        temperature = 0.2
        stream = $false
    } `
    -TimeoutSec 180

$results += Invoke-TimedJson `
    -Name "completion-short" `
    -Uri "$base/completions" `
    -Body @{
        model = $Model
        prompt = "Say hello in exactly five words."
        max_tokens = $MaxTokens
        temperature = 0.2
    } `
    -TimeoutSec 180

$chunk = "Project log: verify local LLM long context behavior, preserve system instructions, maintain Hermes compatibility, and answer concisely with measured facts. "
$longPrompt = ($chunk * $LongPromptRepeats) + "`nQuestion: Summarize the operational instruction in one sentence."
$results += Invoke-TimedJson `
    -Name "chat-long" `
    -Uri "$base/chat/completions" `
    -Body @{
        model = $Model
        messages = @(
            @{ role = "system"; content = "You are a concise benchmark responder." },
            @{ role = "user"; content = $longPrompt }
        )
        max_tokens = $MaxTokens
        temperature = 0.1
        stream = $false
    } `
    -TimeoutSec 600

if ($OutFile) {
    $outDir = Split-Path -Parent $OutFile
    if ($outDir) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }
    $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutFile -Encoding UTF8
}

$results | Format-Table -AutoSize
