#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
API_KEY="${LLM_API_KEY:-local-dev-key}"
CONFIG_DIR="${CODEX_CONFIG_DIR:-$HOME/.codex}"
CONFIG_FILE="${CODEX_CONFIG_FILE:-$CONFIG_DIR/config.toml}"
OVERWRITE="${CODEX_CONFIG_OVERWRITE:-0}"

if [[ -e "$CONFIG_FILE" && "$OVERWRITE" != "1" ]]; then
  echo "Refusing to overwrite existing config: $CONFIG_FILE"
  echo "Set CODEX_CONFIG_OVERWRITE=1 to replace it."
  exit 1
fi

mkdir -p "$CONFIG_DIR"
umask 077
cat > "$CONFIG_FILE" <<EOF
[openai]
base_url = "$BASE_URL"
api_key = "$API_KEY"
EOF

echo "Wrote Codex client config: $CONFIG_FILE"
echo "base_url=$BASE_URL"

