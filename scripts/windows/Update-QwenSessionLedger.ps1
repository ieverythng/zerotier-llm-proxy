param(
    [string]$LedgerPath = "",
    [string]$ActiveGoal = "",
    [string[]]$StableFact = @(),
    [string[]]$Constraint = @(),
    [string[]]$Decision = @(),
    [string[]]$OpenQuestion = @(),
    [string[]]$NextStep = @(),
    [string[]]$ImportantPath = @(),
    [string[]]$Measurement = @(),
    [switch]$PrintPromptBlock
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $LedgerPath) {
    $LedgerPath = Join-Path $repoRoot "_tmp\session-ledger.md"
}

$templatePath = Join-Path $repoRoot "docs\session-ledger-template.md"
$ledgerParent = Split-Path -Parent $LedgerPath
if ($ledgerParent) {
    New-Item -ItemType Directory -Force -Path $ledgerParent | Out-Null
}

if (-not (Test-Path -LiteralPath $LedgerPath)) {
    Copy-Item -LiteralPath $templatePath -Destination $LedgerPath
}

function Add-LedgerItem {
    param(
        [string]$Section,
        [string]$Text
    )

    if (-not $Text) {
        return
    }

    $content = Get-Content -LiteralPath $LedgerPath -Raw
    $heading = "## $Section"
    if ($content -notmatch [regex]::Escape($heading)) {
        Add-Content -LiteralPath $LedgerPath -Value "`n$heading`n"
        $content = Get-Content -LiteralPath $LedgerPath -Raw
    }

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $entry = "- {0} - {1}" -f $stamp, $Text
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([string[]](Get-Content -LiteralPath $LedgerPath))

    $headingIndex = -1
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -eq $heading) {
            $headingIndex = $index
            break
        }
    }

    if ($headingIndex -lt 0) {
        $lines.Add("")
        $lines.Add($heading)
        $lines.Add("")
        $lines.Add($entry)
    }
    else {
        $insertIndex = $lines.Count
        for ($index = $headingIndex + 1; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -like "## *") {
                $insertIndex = $index
                break
            }
        }

        while ($insertIndex -gt ($headingIndex + 1) -and $lines[$insertIndex - 1].Trim() -eq "") {
            $insertIndex--
        }

        $lines.Insert($insertIndex, $entry)
    }

    Set-Content -LiteralPath $LedgerPath -Value $lines -Encoding UTF8
}

foreach ($item in $ActiveGoal) { Add-LedgerItem -Section "Active Goal" -Text $item }
foreach ($item in $StableFact) { Add-LedgerItem -Section "Stable Facts" -Text $item }
foreach ($item in $Constraint) { Add-LedgerItem -Section "Constraints" -Text $item }
foreach ($item in $Decision) { Add-LedgerItem -Section "Decisions" -Text $item }
foreach ($item in $OpenQuestion) { Add-LedgerItem -Section "Open Questions" -Text $item }
foreach ($item in $NextStep) { Add-LedgerItem -Section "Next Steps" -Text $item }
foreach ($item in $ImportantPath) { Add-LedgerItem -Section "Important Paths" -Text $item }
foreach ($item in $Measurement) { Add-LedgerItem -Section "Recent Measurements" -Text $item }

Write-Host "Updated $LedgerPath"

if ($PrintPromptBlock) {
    Write-Host ""
    Write-Host "----- paste into next compacted session -----"
    Get-Content -LiteralPath $LedgerPath
    Write-Host "----- end session ledger -----"
}
