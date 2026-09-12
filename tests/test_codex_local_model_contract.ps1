$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$installer = Join-Path $repo 'scripts\windows\Install-CodexQwen38Catalog.ps1'
$desktopSwitch = Join-Path $repo 'scripts\windows\Set-CodexDesktopWatson.ps1'
$shortcutInstaller = Join-Path $repo 'scripts\windows\Install-CodexWatsonShortcut.ps1'
$appLauncher = Join-Path $repo 'scripts\windows\Start-CodexWatsonApp.ps1'
$router = Join-Path $repo 'scripts\windows\codex_watson_router.py'
$launcher = Join-Path $repo 'scripts\windows\Start-WatsonStack.ps1'

foreach ($path in @($installer, $desktopSwitch, $shortcutInstaller, $appLauncher, $launcher)) {
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors) { throw "$path has parser errors:`n$($errors | Out-String)" }
}

$desktopSource = Get-Content -Raw -LiteralPath $desktopSwitch
if ($desktopSource -notmatch '\$env:USERPROFILE\\\.codex-watson') {
    throw 'Desktop configuration no longer defaults to an isolated Codex home.'
}
$shortcutSource = Get-Content -Raw -LiteralPath $shortcutInstaller
if ($shortcutSource -notlike '*primary_codex_home_changed = $false*') {
    throw 'Shortcut installer does not promise to preserve the primary Codex home.'
}
$appSource = Get-Content -Raw -LiteralPath $appLauncher
foreach ($token in @('127.0.0.1:4010/v1', '127.0.0.1:4000/v1', 'Get-Process -Name ChatGPT')) {
    if ($appSource -notlike "*$token*") { throw "Missing isolated app-launch invariant: $token" }
}
$python = Join-Path $env:USERPROFILE 'itrader\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
& $python -m py_compile $router
if ($LASTEXITCODE -ne 0) { throw 'Watson Codex router failed Python compilation.' }

$installerSource = Get-Content -Raw -LiteralPath $installer
foreach ($token in @('$base.supported_in_api = $true', '$base.use_responses_lite = $false', 'meta.n_ctx')) {
    if ($installerSource -notlike "*$token*") { throw "Missing catalog invariant: $token" }
}
if ((Get-Content -Raw -LiteralPath $launcher) -match 'CodexBridge|codex_responses_bridge') {
    throw 'The canonical launcher still depends on the removed Responses bridge.'
}

$testHome = Join-Path ([IO.Path]::GetTempPath()) ("watson-codex-test-" + [guid]::NewGuid())
try {
    $catalogDir = Join-Path $testHome 'model-catalogs'
    New-Item -ItemType Directory -Path $catalogDir | Out-Null
    $catalogPath = Join-Path $catalogDir 'catalog.json'
    @{
        models = @(@{ slug = 'qwen3.8'; supported_in_api = $true; use_responses_lite = $false })
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $catalogPath
    $original = @"
model_catalog_json = "$($catalogPath.Replace('\', '/'))"
model = "gpt-5.6-sol"
model_provider = "openai"

[windows]
sandbox = "elevated"
"@
    $configPath = Join-Path $testHome 'config.toml'
    Set-Content -LiteralPath $configPath -Value $original

    & $desktopSwitch -CodexHome $testHome -BaseUrl 'http://127.0.0.1:4010/v1'
    $enabled = Get-Content -Raw -LiteralPath $configPath
    if ($enabled -notmatch '(?m)^model = "gpt-5\.6-sol"\r?$') { throw 'Hosted desktop default was not preserved.' }
    if ($enabled -notmatch '(?m)^openai_base_url = "http://127\.0\.0\.1:4010/v1"\r?$') { throw 'Desktop router base URL was not selected.' }
    if ($enabled -match '(?m)^model_provider\s*=') { throw 'Desktop mode retained model_provider.' }
    if ($enabled -match '(?m)^model_context_window\s*=') { throw 'Local context override leaked into hosted models.' }

    & $desktopSwitch -CodexHome $testHome -Restore
    if ((Get-Content -Raw -LiteralPath $configPath) -ne ($original + "`r`n")) {
        throw 'Desktop restore did not reproduce the original configuration.'
    }
}
finally {
    if (Test-Path -LiteralPath $testHome) { Remove-Item -LiteralPath $testHome -Recurse -Force }
}

Write-Output 'PASS: Watson catalog and desktop routing invariants are preserved.'
