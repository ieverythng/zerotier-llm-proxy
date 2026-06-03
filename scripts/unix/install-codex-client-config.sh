#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
CONFIG_DIR="${CODEX_CONFIG_DIR:-$HOME/.codex}"
CONFIG_FILE="${CODEX_CONFIG_FILE:-$CONFIG_DIR/config.toml}"
PROFILE_FILE="${CODEX_PROFILE_FILE:-$CONFIG_DIR/qwen36-zerotier.config.toml}"

mkdir -p "$CONFIG_DIR"
umask 077

if [[ -e "$CONFIG_FILE" ]]; then
  cp "$CONFIG_FILE" "$CONFIG_FILE.backup-$(date +%Y%m%d-%H%M%S)"
else
  : > "$CONFIG_FILE"
fi

python3 - "$CONFIG_FILE" "$BASE_URL" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2]
text = path.read_text(encoding="utf-8")
text = re.sub(r"\n?\[model_providers\.qwen36-zerotier\].*?(?=\n\[[^\]]+\]|\Z)", "", text, flags=re.S)

provider = f"""

[model_providers.qwen36-zerotier]
name = "qwen36 via Windows ZeroTier LiteLLM"
base_url = "{base_url}"
wire_api = "responses"
"""

path.write_text(text.rstrip() + provider + "\n", encoding="utf-8")
PY

if [[ -e "$PROFILE_FILE" ]]; then
  cp "$PROFILE_FILE" "$PROFILE_FILE.backup-$(date +%Y%m%d-%H%M%S)"
fi

cat > "$PROFILE_FILE" <<EOF
model = "qwen36-turbo-hermes"
model_provider = "qwen36-zerotier"
model_context_window = 65536
model_max_output_tokens = 8192
EOF

echo "Registered provider in Codex config: $CONFIG_FILE"
echo "Installed selectable Codex profile: $PROFILE_FILE"
echo "base_url=$BASE_URL"

