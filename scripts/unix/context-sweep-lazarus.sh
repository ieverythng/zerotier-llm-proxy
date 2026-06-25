#!/usr/bin/env bash
# Run Phase 2 context sweep with Lazarus restarts + Watson harness checks.
#
# Default is dry-run so it is safe in a live gateway thread:
#   bash scripts/unix/context-sweep-lazarus.sh --dry-run
# Live sweep (will restart llama.cpp/LiteLLM for each context):
#   bash scripts/unix/context-sweep-lazarus.sh --live --contexts 80000,96000,112000,128000

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTEXTS="80000,96000,112000,128000"
MODE="stable"
MODEL="qwen36-turbo-hermes"
BASE_URL="http://172.24.16.1:4000/v1"
HEADROOM_URL="http://172.24.16.1:8787/health"
DRY_RUN=1
HARNESS_CONTEXT_FRACTION="0.75"
MAX_TOKENS="96"

usage() {
  sed -n '1,12p' "$0"
  echo "Options: --live --dry-run --contexts CSV --mode stable|spec --base-url URL --headroom-url URL --fraction 0.75 --max-tokens N"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live) DRY_RUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --contexts) CONTEXTS="${2:?missing contexts}"; shift 2 ;;
    --mode) MODE="${2:?missing mode}"; shift 2 ;;
    --model) MODEL="${2:?missing model}"; shift 2 ;;
    --base-url) BASE_URL="${2:?missing base url}"; shift 2 ;;
    --headroom-url) HEADROOM_URL="${2:?missing headroom url}"; shift 2 ;;
    --fraction) HARNESS_CONTEXT_FRACTION="${2:?missing fraction}"; shift 2 ;;
    --max-tokens) MAX_TOKENS="${2:?missing max tokens}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

OUT_DIR="$ROOT/reports/benchmarks/context-sweep-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"
SUMMARY="$OUT_DIR/summary.md"
JSONL="$OUT_DIR/runs.jsonl"

IFS=',' read -r -a CTX_ARRAY <<< "$CONTEXTS"
{
  echo "# Context Sweep via Lazarus"
  echo
  echo "Generated: $(date -u +%FT%TZ)"
  echo "Mode: $MODE"
  echo "Dry run: $DRY_RUN"
  echo "Contexts: $CONTEXTS"
  echo
  echo "| Configured ctx | Harness target ctx | Lazarus status | Harness report |"
  echo "|---:|---:|---|---|"
} > "$SUMMARY"

for ctx in "${CTX_ARRAY[@]}"; do
  ctx="${ctx// /}"
  [[ -n "$ctx" ]] || continue
  target=$(python3 - <<PY
ctx=int("$ctx")
f=float("$HARNESS_CONTEXT_FRACTION")
print(max(0, int(ctx*f)))
PY
)
  label="ctx${ctx}"
  echo "== Context $ctx (harness target $target) =="
  lazarus_log="$OUT_DIR/lazarus-${label}.log"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$ROOT/scripts/unix/lazarus.sh" --dry-run --mode "$MODE" --ctx "$ctx" | tee "$lazarus_log"
    lazarus_status="dry-run"
    harness_report="skipped"
  else
    if bash "$ROOT/scripts/unix/lazarus.sh" --mode "$MODE" --ctx "$ctx" | tee "$lazarus_log"; then
      lazarus_status="ok"
      harness_log="$OUT_DIR/harness-${label}.log"
      python3 "$ROOT/scripts/unix/watson-stack-harness.py" \
        --base-url "$BASE_URL" \
        --headroom-url "$HEADROOM_URL" \
        --model "$MODEL" \
        --contexts "$target" \
        --max-tokens "$MAX_TOKENS" \
        --label "$label" | tee "$harness_log"
      harness_report=$(awk '/^MD /{print $2}' "$harness_log" | tail -1)
    else
      lazarus_status="failed"
      harness_report="skipped"
    fi
  fi
  printf '{"ctx":%s,"target":%s,"status":"%s","harness_report":"%s"}\n' "$ctx" "$target" "$lazarus_status" "$harness_report" >> "$JSONL"
  echo "| $ctx | $target | $lazarus_status | $harness_report |" >> "$SUMMARY"
done

echo
printf 'SUMMARY %s\n' "$SUMMARY"
printf 'JSONL %s\n' "$JSONL"
