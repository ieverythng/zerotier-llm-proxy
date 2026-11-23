[CmdletBinding()]
param(
    [ValidateSet('All', 'Engine', 'Python')]
    [string]$Only = 'All',
    [ValidateSet('b12633', 'b12651')]
    [string]$EngineBuild = 'b12633',
    [string]$Python = 'python.exe'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stage = Join-Path $repoRoot '.runtime\local-model-deps'
New-Item -ItemType Directory -Path $stage -Force | Out-Null

function Invoke-CheckedNative {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $Program -Leaf) failed (exit $LASTEXITCODE)." }
}

if ($Only -ne 'Python') {
    $hashes = @{
        b12633 = '5fadbb7d1f4b7e612f77c7b626c60445d5d1fc9d990425ee7f37d5f17a8c68ad'
        b12651 = '5ad87e0771f3a129f6461b5fc706d8c2c5e5b71b2bd1f953a4221ca4d7797fef'
    }
    $cudartHash = 'c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba'
    $base = "https://github.com/ggml-org/llama.cpp/releases/download/$EngineBuild"
    $binaryArchive = "llama-$EngineBuild-bin-win-cuda-13.5-x64.zip"
    $cudaArchive = 'cudart-llama-bin-win-cuda-13.5-x64.zip'
    $downloads = Join-Path $stage 'downloads'
    $engine = Join-Path $stage "llama-$EngineBuild-win-cuda135"
    $executable = Join-Path $engine 'llama-server.exe'
    $receipt = Join-Path $engine '.verified-archives'
    $expectedReceipt = "$($hashes[$EngineBuild]) $cudartHash"
    if (-not ((Test-Path -LiteralPath $executable -PathType Leaf) -and
        (Test-Path -LiteralPath $receipt -PathType Leaf) -and
        (Get-Content -LiteralPath $receipt -Raw).Trim() -eq $expectedReceipt)) {
        New-Item -ItemType Directory -Path $downloads, $engine -Force | Out-Null
        foreach ($asset in @(@{ Name = $binaryArchive; SHA = $hashes[$EngineBuild] }, @{ Name = $cudaArchive; SHA = $cudartHash })) {
            $archive = Join-Path $downloads $asset.Name
            if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
                $partial = "$archive.partial"
                Invoke-CheckedNative 'curl.exe' @('--fail', '--location', '--retry', '2', '--silent', '--show-error', '--output', $partial, "$base/$($asset.Name)")
                if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $asset.SHA) {
                    throw "Downloaded SHA-256 mismatch for $($asset.Name); the archive was not extracted."
                }
                [System.IO.File]::Move($partial, $archive)
            }
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $asset.SHA) {
                throw "Cached SHA-256 mismatch for $($asset.Name); inspect the isolated downloads directory."
            }
            Expand-Archive -LiteralPath $archive -DestinationPath $engine -Force
        }
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
            throw 'The verified binary archive did not contain llama-server.exe at the expected location.'
        }
        Invoke-CheckedNative $executable @('--version')
        Set-Content -LiteralPath $receipt -Value $expectedReceipt -Encoding ASCII
    }
    Write-Host "Verified, staged llama.cpp: $executable"
}

if ($Only -ne 'Engine') {
    $uv = (Get-Command uv.exe -ErrorAction Stop).Source
    $interpreter = (Get-Command $Python -ErrorAction Stop).Source
    foreach ($spec in @(
        @{ Directory = 'tui-rich15.0.0'; Packages = @('rich==15.0.0') },
        @{ Directory = 'litellm1.110.2'; Packages = @('litellm[proxy]==1.110.2') },
        @{ Directory = 'headroom0.45.0'; Packages = @('headroom-ai[proxy]==0.45.0', 'litellm==1.110.2') }
    )) {
        $venv = Join-Path $stage $spec.Directory
        $envPython = Join-Path $venv 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $envPython -PathType Leaf)) {
            Invoke-CheckedNative $uv @('venv', '--python', $interpreter, $venv)
        }
        Invoke-CheckedNative $uv (@('pip', 'install', '--python', $envPython) + $spec.Packages)
        Invoke-CheckedNative $uv @('pip', 'check', '--python', $envPython)
    }
    Write-Host "Staged isolated Python environments under $stage; legacy environments were not changed."
}
