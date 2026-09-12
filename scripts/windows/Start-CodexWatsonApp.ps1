[CmdletBinding()]
param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$RouterBaseUrl = 'http://127.0.0.1:4010/v1',
    [string]$LiteLlmBaseUrl = 'http://127.0.0.1:4000/v1',
    [string]$Python = (Join-Path $env:USERPROFILE 'itrader\python.exe'),
    [switch]$ConfigureOnly
)

$ErrorActionPreference = 'Stop'

if (-not $ConfigureOnly -and (Get-Process -Name ChatGPT -ErrorAction SilentlyContinue)) {
    throw 'Close the running Codex app before using the Watson-enabled shortcut so the startup catalog and route are reloaded.'
}

$configPath = Join-Path $CodexHome 'config.toml'
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Codex configuration not found at '$configPath'."
}

$modelsUri = "$($LiteLlmBaseUrl.TrimEnd('/'))/models"
try {
    $models = Invoke-RestMethod -Uri $modelsUri -Method Get -TimeoutSec 10
} catch {
    throw "Watson LiteLLM is unavailable at '$modelsUri'. Start the Watson stack first. $($_.Exception.Message)"
}
if (-not @($models.data | Where-Object { $_.id -eq 'qwen3.8' })) {
    throw "LiteLLM at '$modelsUri' does not advertise qwen3.8."
}

$routerRoot = $RouterBaseUrl -replace '/v1/?$', ''
$routerHealth = "$routerRoot/_health"
try {
    $routerStatus = Invoke-RestMethod -Uri $routerHealth -TimeoutSec 2
    if ($routerStatus.service -ne 'watson-codex-router') { throw 'Another service is using the Watson router port.' }
} catch {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Python runtime not found at '$Python'." }
    $router = Join-Path $PSScriptRoot 'codex_watson_router.py'
    $routerLog = Join-Path $CodexHome 'watson-router.log'
    $routerErrorLog = Join-Path $CodexHome 'watson-router.error.log'
    $routerPort = ([uri]$RouterBaseUrl).Port
    $routerArgs = @($router, '--local-base', ($LiteLlmBaseUrl -replace '/v1/?$', ''), '--port', $routerPort)
    Start-Process -FilePath $Python -ArgumentList $routerArgs -WindowStyle Hidden -RedirectStandardOutput $routerLog -RedirectStandardError $routerErrorLog
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        try {
            $routerStatus = Invoke-RestMethod -Uri $routerHealth -TimeoutSec 1
            if ($routerStatus.service -eq 'watson-codex-router') { break }
        } catch {}
    } while ([DateTime]::UtcNow -lt $deadline)
    if ([DateTime]::UtcNow -ge $deadline) { throw "Watson Codex router did not become healthy. See '$routerErrorLog'." }
}

$backupPath = Join-Path $CodexHome 'config.before-watson-router.toml'
$current = Get-Content -Raw -LiteralPath $configPath
if ($current -notmatch '(?m)^openai_base_url\s*=\s*"http://127\.0\.0\.1:4010/v1/?"') {
    Copy-Item -LiteralPath $configPath -Destination $backupPath -Force
}

$profileInstaller = Join-Path $PSScriptRoot 'Install-CodexQwen38WatsonProfile.ps1'
$desktopConfigurator = Join-Path $PSScriptRoot 'Set-CodexDesktopWatson.ps1'
& $profileInstaller -CodexHome $CodexHome -BaseUrl $LiteLlmBaseUrl
& $desktopConfigurator -CodexHome $CodexHome -BaseUrl $RouterBaseUrl -DefaultModel 'gpt-5.6-sol' -SkipBackup

if ($ConfigureOnly) {
    [pscustomobject]@{
        config = $configPath
        backup = $backupPath
        router = $RouterBaseUrl
        default_model = 'gpt-5.6-sol'
        local_model = 'qwen3.8'
    } | ConvertTo-Json -Compress
    return
}

$app = Get-StartApps | Where-Object { $_.Name -eq 'ChatGPT' -or $_.Name -like 'ChatGPT*' -or $_.Name -eq 'Codex' -or $_.Name -like 'Codex*' } | Select-Object -First 1
if (-not $app) { throw 'The Codex/ChatGPT desktop app is not registered in the Start menu.' }
Start-Process -FilePath explorer.exe -ArgumentList "shell:AppsFolder\$($app.AppID)"
