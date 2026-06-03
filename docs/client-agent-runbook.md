# Client Agent Runbook

This machine is only a client. The Windows inference host is expected to run `llama.cpp` locally and expose LiteLLM on the ZeroTier interface.

## Install Codex Client Config

```bash
./scripts/unix/install-codex-client-config.sh
```

The script registers `[model_providers.qwen36-zerotier]` in `~/.codex/config.toml` and writes a selectable `~/.codex/qwen36-zerotier.config.toml` profile. It does not change the global default model.

## Verify Proxy

```bash
./scripts/unix/verify-client.sh
```

Useful overrides:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 ./scripts/unix/verify-client.sh
LLM_MODEL=qwen36-turbo-hermes ./scripts/unix/verify-client.sh
```

## Expected Success

- `/v1/models` returns `qwen36-turbo-hermes`.
- `/v1/chat/completions` returns a small text response.
- `/v1/responses` returns a Codex-compatible response or at least a valid JSON response from LiteLLM.

## Common Client-Side Failures

- `Connection refused`: LiteLLM is not running, is not bound to `0.0.0.0`, or Windows Firewall blocks TCP 4000.
- `Timeout`: ZeroTier route/firewall issue or the host is offline. If `ip route get 10.88.140.94` shows your Wi-Fi gateway instead of a ZeroTier device, run `sudo zerotier-cli set 3b19b3a716937e29 allowManaged=1`, reconnect ZeroTier, and verify again.
- `401`/credential errors: the proxy is probably running with a stale authenticated config; restart it from `scripts/windows/Start-Qwen36LiteLLM.ps1`.
- Codex websocket warnings: Codex is likely not reading `~/.codex/config.toml` or is pointed at the raw `llama.cpp` server instead of LiteLLM.

## Run Codex With Qwen

```bash
codex exec --profile qwen36-zerotier "Say hello"
```

## Run Codex App With Qwen

Restart Codex Desktop after running the installer. The app reads the provider registry from `~/.codex/config.toml`; do not use a temporary `CODEX_HOME` wrapper for the app.

