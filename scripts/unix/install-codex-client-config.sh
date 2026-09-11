#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
LLAMA_BASE_URL="${LLAMA_CPP_BASE_URL:-http://172.24.16.1:8080/v1}"
CONTEXT_WINDOW="${LLM_CONTEXT_WINDOW:-}"
if [[ -z "$CONTEXT_WINDOW" ]] && command -v curl >/dev/null 2>&1; then
  CONTEXT_WINDOW="$(curl -fsS --max-time 10 "$LLAMA_BASE_URL/models" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(int((d.get("data") or [{}])[0].get("meta",{}).get("n_ctx",0)))' \
    2>/dev/null || true)"
fi
CONTEXT_WINDOW="${CONTEXT_WINDOW:-65536}"
MAX_CONTEXT_WINDOW="${LLM_MAX_CONTEXT_WINDOW:-$CONTEXT_WINDOW}"
CONFIG_DIR="${CODEX_CONFIG_DIR:-$HOME/.codex}"
CONFIG_FILE="${CODEX_CONFIG_FILE:-$CONFIG_DIR/config.toml}"
PROFILE_FILE="${CODEX_PROFILE_FILE:-$CONFIG_DIR/qwen38-zerotier.config.toml}"
CATALOG_FILE="${CODEX_MODEL_CATALOG_FILE:-$CONFIG_DIR/model-catalogs/qwen38-plus-bundled.json}"

mkdir -p "$CONFIG_DIR"
umask 077

if [[ -e "$CONFIG_FILE" ]]; then
  cp "$CONFIG_FILE" "$CONFIG_FILE.backup-$(date +%Y%m%d-%H%M%S)"
else
  : > "$CONFIG_FILE"
fi

python3 - "$CONFIG_FILE" "$BASE_URL" "$CATALOG_FILE" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2]
catalog_file = sys.argv[3]
text = path.read_text(encoding="utf-8")
text = re.sub(r"\n?\[model_providers\.qwen36-zerotier\].*?(?=\n\[[^\]]+\]|\Z)", "", text, flags=re.S)
text = re.sub(r"\n?\[profiles\.qwen36-zerotier\].*?(?=\n\[[^\]]+\]|\Z)", "", text, flags=re.S)

catalog_line = f'model_catalog_json = "{catalog_file}"'
if re.search(r"^model_catalog_json\s*=", text, flags=re.M):
    text = re.sub(r"^model_catalog_json\s*=.*$", catalog_line, text, count=1, flags=re.M)
else:
    text = catalog_line + "\n" + text

provider = f"""

[model_providers.qwen36-zerotier]
name = "qwen36 via Windows ZeroTier LiteLLM"
base_url = "{base_url}"
wire_api = "responses"
"""

path.write_text(text.rstrip() + provider + "\n", encoding="utf-8")
PY

mkdir -p "$(dirname "$CATALOG_FILE")"
TMP_CATALOG="$(mktemp)"
if codex debug models --bundled > "$TMP_CATALOG"; then
  python3 - "$TMP_CATALOG" "$CATALOG_FILE" "$CONTEXT_WINDOW" "$MAX_CONTEXT_WINDOW" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
context_window = int(sys.argv[3])
max_context_window = int(sys.argv[4])
data = json.loads(source.read_text(encoding="utf-8"))
base = dict(data["models"][0])
base.update(
    {
        "slug": "qwen3.8",
        "display_name": "Qwen3.8 (Watson)",
        "description": "Windows-hosted Qwen3.8 served through ZeroTier LiteLLM; context is read from live llama.cpp metadata.",
        "default_reasoning_level": "low",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balanced local reasoning"},
        ],
        "priority": 1,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": None,
        "context_window": context_window,
        "max_context_window": max_context_window,
    }
)
data["models"] = [m for m in data["models"] if m.get("slug") not in {"qwen3.8", "qwen36-turbo-hermes"}]
data["models"].append(base)
target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
else
  echo "Warning: could not generate model catalog with codex debug models --bundled" >&2
fi
rm -f "$TMP_CATALOG"

if [[ -e "$PROFILE_FILE" ]]; then
  cp "$PROFILE_FILE" "$PROFILE_FILE.backup-$(date +%Y%m%d-%H%M%S)"
fi

cat > "$PROFILE_FILE" <<EOF
model = "qwen3.8"
model_provider = "qwen36-zerotier"
model_context_window = $CONTEXT_WINDOW
model_max_output_tokens = 8192
EOF

echo "Registered provider in Codex config: $CONFIG_FILE"
echo "Installed selectable Codex profile: $PROFILE_FILE"
echo "Installed merged model catalog: $CATALOG_FILE"
echo "base_url=$BASE_URL"
echo "context_window=$CONTEXT_WINDOW"
