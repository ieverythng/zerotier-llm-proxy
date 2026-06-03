# Client Agent Runbook

This machine is only a client. The Windows inference host is expected to run `llama.cpp` locally and expose LiteLLM on the ZeroTier interface.

## Install Codex Client Config

```bash
./scripts/unix/install-codex-client-config.sh
```

The script writes `~/.codex/config.toml`. It refuses to overwrite an existing config unless `CODEX_CONFIG_OVERWRITE=1` is set.

## Verify Proxy

```bash
./scripts/unix/verify-client.sh
```

Useful overrides:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 ./scripts/unix/verify-client.sh
LLM_MODEL=qwen36-turbo-hermes ./scripts/unix/verify-client.sh
LLM_API_KEY=local-dev-key ./scripts/unix/verify-client.sh
```

## Expected Success

- `/v1/models` returns `qwen36-turbo-hermes`.
- `/v1/chat/completions` returns a small text response.
- `/v1/responses` returns a Codex-compatible response or at least a valid JSON response from LiteLLM.

## Common Client-Side Failures

- `Connection refused`: LiteLLM is not running, is not bound to `0.0.0.0`, or Windows Firewall blocks TCP 4000.
- `Timeout`: ZeroTier route/firewall issue or the host is offline.
- `401`/credential errors: set `LLM_API_KEY` to any non-empty value matching what the proxy expects.
- Codex websocket warnings: Codex is likely not reading `~/.codex/config.toml` or is pointed at the raw `llama.cpp` server instead of LiteLLM.

