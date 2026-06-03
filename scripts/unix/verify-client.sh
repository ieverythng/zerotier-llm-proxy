#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${LLM_PROXY_BASE_URL:-http://10.88.140.94:4000/v1}"
MODEL="${LLM_MODEL:-qwen36-turbo-hermes}"
TIMEOUT_SECONDS="${LLM_VERIFY_TIMEOUT_SECONDS:-30}"

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

curl_json() {
  local method="$1"
  local url="$2"
  local data="${3:-}"

  if [[ -n "$data" ]]; then
    curl --fail --show-error --silent \
      --max-time "$TIMEOUT_SECONDS" \
      -X "$method" "$url" \
      -H "Content-Type: application/json" \
      --data "$data"
  else
    curl --fail --show-error --silent \
      --max-time "$TIMEOUT_SECONDS" \
      -X "$method" "$url" \
      -H "Content-Type: application/json"
  fi
}

require curl

echo "Checking LiteLLM proxy at $BASE_URL"

echo
echo "1. GET /models"
MODELS_JSON="$(curl_json GET "$BASE_URL/models")"
echo "$MODELS_JSON"

if [[ "$MODELS_JSON" != *"$MODEL"* ]]; then
  echo "Warning: model '$MODEL' was not visible in /models output." >&2
fi

echo
echo "2. POST /chat/completions"
CHAT_PAYLOAD='{"model":"'"$MODEL"'","messages":[{"role":"user","content":"Reply with exactly: client-ok"}],"max_tokens":16,"temperature":0}'
curl_json POST "$BASE_URL/chat/completions" "$CHAT_PAYLOAD"

echo
echo
echo "3. POST /responses"
RESPONSES_PAYLOAD='{"model":"'"$MODEL"'","input":"Reply with exactly: responses-ok","max_output_tokens":16,"temperature":0}'
curl_json POST "$BASE_URL/responses" "$RESPONSES_PAYLOAD"

echo
echo
echo "Client verification finished."

