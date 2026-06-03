param(
    [string]$BaseUrl = "http://127.0.0.1:4000/v1",
    [string]$ApiKey = "local-qwen36",
    [string]$Model = "qwen36-turbo-hermes"
)

$ErrorActionPreference = "Stop"
$headers = @{}
if ($ApiKey) {
    $headers.Authorization = "Bearer $ApiKey"
}

Write-Host "Checking models at $BaseUrl/models"
$models = Invoke-RestMethod -Uri "$BaseUrl/models" -Headers $headers -TimeoutSec 15
$models | ConvertTo-Json -Depth 8

Write-Host "Checking Responses API at $BaseUrl/responses"
$body = @{
    model = $Model
    input = "Reply with exactly: qwen36 proxy ok"
    max_output_tokens = 32
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod -Uri "$BaseUrl/responses" -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 120
$response | ConvertTo-Json -Depth 12
