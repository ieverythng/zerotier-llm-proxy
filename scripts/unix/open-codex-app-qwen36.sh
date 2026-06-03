#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
WORKSPACE="${1:-.}"
SOURCE_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SESSION_CODEX_HOME="${CODEX_QWEN_SESSION_HOME:-${TMPDIR:-/tmp}/codex-qwen36-home}"

rm -rf "$SESSION_CODEX_HOME"
mkdir -p "$SESSION_CODEX_HOME"

if [[ -f "$SOURCE_CODEX_HOME/config.toml" ]]; then
  cp "$SOURCE_CODEX_HOME/config.toml" "$SESSION_CODEX_HOME/config.toml"
else
  : > "$SESSION_CODEX_HOME/config.toml"
fi

python3 - "$SESSION_CODEX_HOME/config.toml" "$BASE_URL" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2]
text = path.read_text(encoding="utf-8")

for table in ("model_providers.qwen36-zerotier", "profiles.qwen36-zerotier"):
    text = re.sub(rf"\n?\[{re.escape(table)}\].*?(?=\n\[[^\]]+\]|\Z)", "", text, flags=re.S)

def set_top_level(src: str, key: str, value: str) -> str:
    line = f"{key} = {value}"
    if re.search(rf"^{re.escape(key)}\s*=", src, flags=re.M):
        return re.sub(rf"^{re.escape(key)}\s*=.*$", line, src, count=1, flags=re.M)
    return line + "\n" + src

text = set_top_level(text, "model", '"qwen36-turbo-hermes"')
text = set_top_level(text, "model_provider", '"qwen36-zerotier"')
text = set_top_level(text, "model_context_window", "65536")
text = set_top_level(text, "model_max_output_tokens", "8192")

provider = f"""

[model_providers.qwen36-zerotier]
name = "qwen36 via Windows ZeroTier LiteLLM"
base_url = "{base_url}"
wire_api = "responses"
"""

path.write_text(text.rstrip() + provider + "\n", encoding="utf-8")
PY

export CODEX_HOME="$SESSION_CODEX_HOME"
exec codex app "$WORKSPACE"
