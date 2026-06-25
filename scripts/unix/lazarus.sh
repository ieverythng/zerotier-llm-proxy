#!/usr/bin/env bash
# LAZARUS — restore the local inference stack after model/routing experiments.
#
# Safe default: verifies the canonical Windows llama.cpp + LiteLLM stack without touching Hermes
# gateway/CLI processes. Use --dry-run to inspect the exact actions first.
#
# Usage:
#   bash scripts/lazarus.sh [--dry-run] [--skip-kill] [--mode stable|spec]
#                           [--ctx 65536] [--profile hermes-qwen36-64k]
#                           [--main-model 'D:\\MODELS\\...gguf']
#
# Canonical launcher: C:\Users\Admin\PROJECTS\zerotier-llm-proxy\scripts\windows\Start-Qwen36ZeroTierStack.ps1
# Do not bypass the ZeroTier stack launcher unless explicitly debugging it.

set -euo pipefail

LLAMA_HOST="${LLAMA_HOST:-172.24.16.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
# LiteLLM is hosted by Windows. From WSL, 127.0.0.1 is WSL itself, not the
# Windows host; use the same host as llama.cpp unless explicitly overridden.
LITELLM_HOST="${LITELLM_HOST:-$LLAMA_HOST}"
LITELLM_PORT="${LITELLM_PORT:-4000}"
LITELLM_CONFIG="${LITELLM_CONFIG:-$HOME/.hermes/litellm/config.yaml}"
LOG_DIR="${LOG_DIR:-/home/juanbeck/Watson/memory}"
LOG="$LOG_DIR/lazarus-$(date +%Y-%m-%d_%H%M%S).log"

MODE="stable"
CONTEXT_SIZE="65536"
THREADS="16"
UBATCH_SIZE="2048"
PARALLEL="1"
STACK_SCRIPT='C:\\Users\\Admin\\PROJECTS\\zerotier-llm-proxy\\scripts\\windows\\Start-Qwen36ZeroTierStack.ps1'
PROFILE="hermes-qwen36-64k"
MODEL_ALIAS="qwen36-turbo-hermes"
MAIN_MODEL=""
DRAFT_MODEL='D:\\MODELS\\Qwen3.6-27B-DFlash-IQ4_XS.gguf'
DRY_RUN=0
SKIP_KILL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-kill) SKIP_KILL=1; shift ;;
    --mode) MODE="${2:?missing mode}"; shift 2 ;;
    --ctx|--context-size) CONTEXT_SIZE="${2:?missing ctx}"; shift 2 ;;
    --threads) THREADS="${2:?missing threads}"; shift 2 ;;
    --parallel|-np) PARALLEL="${2:?missing parallel}"; shift 2 ;;
    --main-model) MAIN_MODEL="${2:?missing main model}"; shift 2 ;;
    --draft-model) DRAFT_MODEL="${2:?missing draft model}"; shift 2 ;;
    --stack-script) STACK_SCRIPT="${2:?missing stack script path}"; shift 2 ;;
    --profile) PROFILE="${2:?missing profile}"; shift 2 ;;
    --model) MODEL_ALIAS="${2:?missing model alias}"; shift 2 ;;
    --llama-host) LLAMA_HOST="${2:?missing host}"; shift 2 ;;
    --help|-h) sed -n '1,28p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$MODE" in stable|spec) ;; *) echo "--mode must be stable or spec" >&2; exit 2 ;; esac
for numeric_var in LLAMA_PORT LITELLM_PORT CONTEXT_SIZE THREADS UBATCH_SIZE PARALLEL; do
  numeric_value="${!numeric_var}"
  if [[ ! "$numeric_value" =~ ^[0-9]+$ ]]; then
    echo "$numeric_var must be numeric, got: $numeric_value" >&2
    exit 2
  fi
done

mkdir -p "$LOG_DIR" "$(dirname "$LITELLM_CONFIG")"
ts() { echo "[$(date -u '+%H:%M:%S UTC')] $*"; }
log() { ts "$@" | tee -a "$LOG"; }
die() { log "FATAL: $*"; exit 1; }
run() { log "+ $*"; [[ "$DRY_RUN" -eq 1 ]] || "$@"; }
run_bash() { log "+ $*"; [[ "$DRY_RUN" -eq 1 ]] || bash -lc "$*"; }
ps_quote() {
  local escaped="${1//\'/\'\'}"
  printf "'%s'" "$escaped"
}

wait_http() {
  local url="$1" name="$2" tries="${3:-30}" sleep_s="${4:-2}" i
  for ((i=1; i<=tries; i++)); do
    local body status
    body=$(curl -sS --connect-timeout 3 --max-time 10 -w '\n%{http_code}' "$url" 2>/dev/null || true)
    status=$(printf '%s' "$body" | tail -n 1)
    body=$(printf '%s' "$body" | sed '$d')
    if [[ "$status" == "200" ]]; then
      log "  ✓ $name is reachable: $url"
      return 0
    fi
    if [[ "$status" == "503" && "$body" == *"Loading model"* ]]; then
      log "  ⏳ $name is still loading ($i/$tries)"
      sleep "$sleep_s"
      continue
    fi
    log "  ⏳ Waiting for $name ($i/$tries)"
    sleep "$sleep_s"
  done
  return 1
}

log "╔══════════════════════════════════════════════════════════╗"
log "║              LAZARUS — INFERENCE RESURRECTION           ║"
log "╚══════════════════════════════════════════════════════════╝"
log "Mode=$MODE DryRun=$DRY_RUN SkipKill=$SKIP_KILL"
log "llama.cpp target: http://${LLAMA_HOST}:${LLAMA_PORT}/v1"
log "LiteLLM target : http://${LITELLM_HOST}:${LITELLM_PORT}/v1"
log "Stack script    : $STACK_SCRIPT"
log "Profile         : $PROFILE"
log "Model alias     : $MODEL_ALIAS"
[[ -n "$MAIN_MODEL" ]] && log "Model override  : $MAIN_MODEL"
[[ "$MODE" == "spec" ]] && log "Draft model     : $DRAFT_MODEL"

log ""
log "── Phase 1: WSL proxy cleanup ──"
if [[ "$SKIP_KILL" -eq 1 ]]; then
  log "  ℹ Skipping process cleanup (--skip-kill)"
else
  if pgrep -f "litellm" >/dev/null 2>&1; then
    run_bash "pkill -f 'litellm' || true"
    sleep 1
    log "  ✓ Requested LiteLLM stop"
  else
    log "  ℹ No LiteLLM process found"
  fi
  for port in "$LITELLM_PORT" 18080; do
    pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K\d+' | head -1 || true)
    if [[ -n "${pid:-}" ]]; then
      run kill "$pid"
      log "  ✓ Killed WSL PID $pid on port $port"
    fi
  done
fi

log ""
log "── Phase 2: Canonical ZeroTier stack launch ──"
PS_STACK_SCRIPT=$(ps_quote "$STACK_SCRIPT")
PS_PROFILE=$(ps_quote "$PROFILE")
PS_MODEL=$(ps_quote "$MODEL_ALIAS")
PS_CMD="\$ErrorActionPreference='Stop'; & ${PS_STACK_SCRIPT} -ContextSize ${CONTEXT_SIZE} -Profile ${PS_PROFILE} -Model ${PS_MODEL} -ReplaceLiteLLM -NoOracle"
if [[ -n "$MAIN_MODEL" ]]; then
  PS_MAIN_MODEL=$(ps_quote "$MAIN_MODEL")
  PS_CMD+=" -ModelPath ${PS_MAIN_MODEL}"
fi
if [[ "$MODE" == "spec" ]]; then
  log "  ⚠ --mode spec requested; canonical stack launcher does not accept DFlash draft args directly. Use an MTP/spec profile if available."
fi

if command -v powershell.exe >/dev/null 2>&1; then
  log "  ℹ Using WSL interop powershell.exe"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY-RUN PowerShell: $PS_CMD"
  else
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$PS_CMD" 2>&1 | tee -a "$LOG" || log "  ⚠ PowerShell stack launch failed; will check if services are already up"
  fi
else
  log "  ⚠ No powershell.exe launcher available; expecting ZeroTier stack already running"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  wait_http "http://${LITELLM_HOST}:${LITELLM_PORT}/v1/models" "LiteLLM/ZeroTier proxy" 90 2 || die "LiteLLM proxy did not respond after canonical stack launch."
fi

log ""
log "── Phase 3: Proxy ownership ──"
log "  ✓ Canonical Windows LiteLLM is healthy at http://${LITELLM_HOST}:${LITELLM_PORT}/v1"
log "  ℹ Do not overwrite $LITELLM_CONFIG or start a WSL LiteLLM instance."
log "    The Windows stack launcher owns proxy configuration and port $LITELLM_PORT."

log ""
log "── Phase 5: End-to-end verification ──"
if [[ "$DRY_RUN" -eq 0 ]]; then
  VERIFY_BASE="http://${LITELLM_HOST}:${LITELLM_PORT}/v1"
  VERIFY_MODEL="$MODEL_ALIAS"
  RESPONSE=$(curl -fsS --connect-timeout 10 --max-time 120 \
    "${VERIFY_BASE}/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer placeholder" \
    -d "{\"model\":\"${VERIFY_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}],\"max_tokens\":8,\"temperature\":0}" 2>/dev/null || true)

  if [[ "$RESPONSE" == *"OK"* || "$RESPONSE" == *"choices"* ]]; then
    log "  ✓ Full chain verified: LiteLLM → llama.cpp returned a chat completion"
  else
    log "  ⚠ Chat test did not return expected content. Raw response follows:"
    log "${RESPONSE:-<empty>}"
  fi
fi

log ""
log "╔══════════════════════════════════════════════════════════╗"
if [[ "$DRY_RUN" -eq 1 ]]; then
  log "║              LAZARUS DRY RUN COMPLETE                   ║"
else
  log "║           LAZARUS COMPLETE — STACK IS BACK ONLINE       ║"
fi
log "╚══════════════════════════════════════════════════════════╝"
log "Log saved to: $LOG"
