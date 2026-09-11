[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8080/v1",
    [string]$Model = "qwen3.8",
    [string]$ApiKey = "",
    [int]$TimeoutSec = 30,
    [int]$Repetitions = 2
)

$ErrorActionPreference = "Stop"
$headers = @{}
if ($ApiKey) { $headers.Authorization = "Bearer $ApiKey" }
$marker = "WATSON_READY_71429"
$limit = 24
for ($attempt = 1; $attempt -le $Repetitions; $attempt++) {
    $body = @{
        model = $Model
        messages = @(@{ role = "user"; content = "Reply with exactly $marker and nothing else." })
        temperature = 0
        max_tokens = $limit
        stream = $false
    } | ConvertTo-Json -Depth 6
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd('/') + '/chat/completions') `
        -Method Post -Headers $headers -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec $TimeoutSec
    $timer.Stop()
    $content = [string]$response.choices[0].message.content
    if ($content.Trim() -ne $marker) {
        throw "Chat coherence check failed at $BaseUrl (attempt $attempt): expected exact readiness marker."
    }
    if ($null -eq $response.usage.completion_tokens -or $response.usage.completion_tokens -gt $limit) {
        throw "Chat token-limit check failed at $BaseUrl (attempt $attempt)."
    }
    [pscustomobject]@{
        Endpoint = $BaseUrl
        Attempt = $attempt
        Passed = $true
        ElapsedMs = $timer.ElapsedMilliseconds
        CompletionTokens = $response.usage.completion_tokens
    }
}
