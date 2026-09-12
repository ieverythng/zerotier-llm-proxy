[CmdletBinding()]
param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$LiteLlmBaseUrl = 'http://127.0.0.1:4000/v1',
    [string]$RouterBaseUrl = 'http://127.0.0.1:4010/v1',
    [string]$ShortcutPath
)

$ErrorActionPreference = 'Stop'
if (-not $ShortcutPath) {
    $localDesktop = Join-Path $env:USERPROFILE 'Desktop'
    $desktop = if (Test-Path -LiteralPath $localDesktop) {
        $localDesktop
    } else {
        [Environment]::GetFolderPath('Desktop')
    }
    $ShortcutPath = Join-Path $desktop 'Codex - Watson Enabled.lnk'
}
$launcher = Join-Path $PSScriptRoot 'Start-CodexWatsonApp.ps1'

foreach ($path in @($launcher)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required script not found: $path" }
}

if (-not (Test-Path -LiteralPath (Join-Path $CodexHome 'config.toml'))) {
    throw "Codex config not found under '$CodexHome'."
}

$package = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue |
    Sort-Object Version -Descending |
    Select-Object -First 1
if (-not $package) { throw 'The Codex desktop app package is not installed.' }
$iconPath = Join-Path $package.InstallLocation 'app\ChatGPT.exe'

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -CodexHome "{1}" -RouterBaseUrl "{2}" -LiteLlmBaseUrl "{3}"' -f $launcher, $CodexHome, $RouterBaseUrl, $LiteLlmBaseUrl
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = Split-Path $launcher -Parent
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'Codex with hosted models plus local Watson through a safe model router'
$shortcut.Save()

[pscustomobject]@{
    shortcut = $ShortcutPath
    codex_home = $CodexHome
    router_base_url = $RouterBaseUrl
    watson_base_url = $LiteLlmBaseUrl
    primary_codex_home_changed = $false
} | ConvertTo-Json -Compress
