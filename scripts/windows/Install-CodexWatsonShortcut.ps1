[CmdletBinding()]
param(
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$LiteLlmBaseUrl = 'http://127.0.0.1:4000/v1',
    [string]$RouterBaseUrl = 'http://127.0.0.1:4010/v1',
    [string]$ShortcutPath
)

$ErrorActionPreference = 'Stop'
if (-not $ShortcutPath) {
    $shellDesktop = [Environment]::GetFolderPath('Desktop')
    $localDesktop = Join-Path $env:USERPROFILE 'Desktop'
    $desktop = if ($shellDesktop -and (Test-Path -LiteralPath $shellDesktop)) {
        $shellDesktop
    } elseif (Test-Path -LiteralPath $localDesktop) {
        $localDesktop
    } else {
        throw 'Windows Desktop folder could not be resolved.'
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

$package = $null
try {
    $package = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop |
        Sort-Object Version -Descending |
        Select-Object -First 1
} catch {
    if ($PSVersionTable.PSEdition -eq 'Core') {
        $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $installLocation = & $windowsPowerShell -NoProfile -Command `
            "(Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1).InstallLocation"
        if ($LASTEXITCODE -eq 0 -and $installLocation) {
            $package = [pscustomobject]@{ InstallLocation = $installLocation.Trim() }
        }
    }
}
if (-not $package) { throw 'The Codex desktop app package is not installed.' }
$iconPath = Join-Path $package.InstallLocation 'app\ChatGPT.exe'

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -CodexHome "{1}" -RouterBaseUrl "{2}" -LiteLlmBaseUrl "{3}"' -f $launcher, $CodexHome, $RouterBaseUrl, $LiteLlmBaseUrl
$shell = New-Object -ComObject WScript.Shell
$buildPath = $ShortcutPath
$copyAfterBuild = $ShortcutPath -match '[^\x00-\x7F]'
if ($copyAfterBuild) {
    $buildPath = Join-Path $env:TEMP "Codex-Watson-Shortcut-$PID.lnk"
}

try {
    $shortcut = $shell.CreateShortcut($buildPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = Split-Path $launcher -Parent
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = 'Codex with hosted models plus local Watson through a safe model router'
    $shortcut.Save()
    if ($copyAfterBuild) {
        Copy-Item -LiteralPath $buildPath -Destination $ShortcutPath -Force
    }
} finally {
    if ($copyAfterBuild -and (Test-Path -LiteralPath $buildPath)) {
        Remove-Item -LiteralPath $buildPath -Force
    }
}

[pscustomobject]@{
    shortcut = $ShortcutPath
    codex_home = $CodexHome
    router_base_url = $RouterBaseUrl
    watson_base_url = $LiteLlmBaseUrl
    primary_codex_home_changed = $false
} | ConvertTo-Json -Compress
