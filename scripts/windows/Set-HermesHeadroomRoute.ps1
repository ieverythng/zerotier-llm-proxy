[CmdletBinding(DefaultParameterSetName = "Enable")]
param(
    [Parameter(ParameterSetName = "Enable")]
    [switch]$Enable,
    [Parameter(ParameterSetName = "Disable")]
    [switch]$Disable,
    [int]$Port = 8787,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$target = if ($Disable) { "http://172.24.16.1:4000/v1" } else { "http://172.24.16.1:$Port/v1" }
$python = @"
from pathlib import Path
import shutil
from datetime import datetime

path = Path('/home/juanbeck/.hermes/config.yaml')
backup = path.with_name(f"config.yaml.bak-headroom-route-{datetime.now():%Y%m%d_%H%M%S}")
shutil.copy2(path, backup)
lines = path.read_text().splitlines()
inside = False
updated = False
for index, line in enumerate(lines):
    if line == '  watson-llama:':
        inside = True
        continue
    if inside and line.startswith('  ') and not line.startswith('    '):
        inside = False
    if inside and line.startswith('    api:'):
        lines[index] = '    api: $target'
        updated = True
        break
if not updated:
    raise SystemExit('Could not find providers.watson-llama.api')
path.write_text(chr(10).join(lines) + chr(10))
print(f'Updated Hermes route to $target')
print(f'Backup: {backup}')
"@

$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
wsl.exe -d Ubuntu -- bash -lc "echo $encoded | base64 -d | python3"

if (-not $NoRestart) {
    wsl.exe -d Ubuntu -- bash -lc "hermes gateway restart"
}
