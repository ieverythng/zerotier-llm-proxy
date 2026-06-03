#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
CONFIG_DIR="${CODEX_CONFIG_DIR:-$HOME/.codex}"
CONFIG_FILE="${CODEX_CONFIG_FILE:-$CONFIG_DIR/qwen36-zerotier.config.toml}"

mkdir -p "$CONFIG_DIR"
umask 077

if [[ -e "$CONFIG_FILE" ]]; then
  cp "$CONFIG_FILE" "$CONFIG_FILE.backup-$(date +%Y%m%d-%H%M%S)"
fi

cat > "$CONFIG_FILE" <<EOF
model = "qwen36-turbo-hermes"
model_provider = "qwen36-zerotier"
model_context_window = 65536
model_max_output_tokens = 8192

[model_providers.qwen36-zerotier]
name = "qwen36 via Windows ZeroTier LiteLLM"
base_url = "$BASE_URL"
wire_api = "responses"
EOF

echo "Installed selectable Codex profile: $CONFIG_FILE"
echo "base_url=$BASE_URL"

