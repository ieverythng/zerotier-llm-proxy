[CmdletBinding()]
param(
    [string]$PluginSource = "C:\tmp\headroom-review\headroom\plugins\hermes\headroom_retrieve"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PluginSource)) {
    throw "Headroom Hermes plugin source was not found: $PluginSource"
}

$wslSource = "/mnt/" + ($PluginSource.Substring(0, 1).ToLower()) + $PluginSource.Substring(2).Replace("", "/")
$python = @"
from pathlib import Path
import shutil
from datetime import datetime

source = Path('$wslSource')
destination = Path.home() / '.hermes/plugins/headroom_retrieve'
if destination.exists():
    shutil.rmtree(destination)
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination)

config = Path.home() / '.hermes/config.yaml'
backup = config.with_name(f"config.yaml.bak-headroom-plugin-{datetime.now():%Y%m%d_%H%M%S}")
shutil.copy2(config, backup)
text = config.read_text()
if '\n- headroom\n' not in text:
    text = text.replace('toolsets:\n- hermes-cli\n', 'toolsets:\n- hermes-cli\n- headroom\n', 1)
text = text.replace('plugins:\n  enabled: []', 'plugins:\n  enabled:\n  - headroom_retrieve', 1)
config.write_text(text)
print(f'Installed plugin: {destination}')
print(f'Config backup: {backup}')
"@

$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
wsl.exe -d Ubuntu -- bash -lc "echo $encoded | base64 -d | python3"
if ($LASTEXITCODE -ne 0) {
    throw "Hermes plugin installation failed."
}
wsl.exe -d Ubuntu -- bash -lc "hermes gateway restart"
