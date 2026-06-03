# ZeroTier LLM Proxy

Role-aware setup repo for sharing one Windows-hosted `llama.cpp` model with Linux/macOS Codex clients over ZeroTier.

The implementation source of truth is [`zerotier-llm-bootstrap.html`](zerotier-llm-bootstrap.html). This repo turns that guide into copy-pasteable config and small verification scripts.

## Roles

- **Client agents, Linux/macOS:** use `config/codex.client.toml.example` and `scripts/unix/*`.
- **Server agent, Windows:** own the LiteLLM and `llama.cpp` host setup. See `docs/server-agent-handoff.md`; server scripts/config should live under `scripts/windows/` and `config/server/` when added.

## Current Client Values

- Windows ZeroTier host: `10.88.140.94`
- LiteLLM proxy: `http://10.88.140.94:4000/v1`
- Model name: `qwen36-turbo-hermes`
- Client auth token: none; access is scoped by ZeroTier and Windows Firewall.
- Codex registry: provider is added to `~/.codex/config.toml`, without changing the global default.
- Codex CLI selection: profile is installed as `~/.codex/qwen36-zerotier.config.toml`.

## Linux Client Quick Start

```bash
./scripts/unix/install-codex-client-config.sh
./scripts/unix/verify-client.sh
codex exec --profile qwen36-zerotier "Say hello"
```

For the Codex desktop app on Linux/macOS, restart the app after installing config. The app should see the custom `qwen36-zerotier` provider from the normal config registry.

Override defaults when needed:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 \
./scripts/unix/install-codex-client-config.sh
```

## Client Verification Only

From this Linux machine, the verification script checks the server from the outside:

- ZeroTier TCP reachability to `:4000`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

It does not start LiteLLM, install server dependencies, open firewall rules, or touch `llama.cpp`.

## Windows Server Quick Start

The Windows host runs `llama.cpp` locally on `127.0.0.1:8080` and exposes LiteLLM on `0.0.0.0:4000`:

```powershell
.\scripts\windows\Start-Qwen36LiteLLM.ps1
.\scripts\windows\Test-Qwen36Proxy.ps1
```

To add the selectable profile to this Windows Codex install without changing the default model:

```powershell
.\scripts\windows\Install-CodexQwen36Config.ps1
```

Restart Codex Desktop after installing the provider so the model/provider registry is reloaded.

