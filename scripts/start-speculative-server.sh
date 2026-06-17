#!/bin/bash
# Start speculative decoding server on Windows PC via WSL interop.
# Launches llama-server.exe with DFlash draft model through PowerShell.
#
# Usage:
#   ./start-speculative-server.sh [beellama|lucebox]
#
# Modes:
#   beellama - Uses Qwen3.6-27B-DFlash-IQ4_XS.gguf (892MB, native DFlash)
#   lucebox  - Uses dflash-draft-3.6-q4_k_m.gguf (1GB, Q4_K_M quant)

set -euo pipefail

MODE="${1:-beellama}"

PS1_SCRIPT="/home/juanbeck/Watson/scripts/start-speculative-server.ps1"

if [[ ! -f "$PS1_SCRIPT" ]]; then
    echo "ERROR: PowerShell script not found at $PS1_SCRIPT"
    exit 1
fi

echo "Starting speculative server (mode: $MODE)..."
echo ""

# Run via PowerShell on Windows host through WSL interop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PS1_SCRIPT" -Mode "$MODE"
