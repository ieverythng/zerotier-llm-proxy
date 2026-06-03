# ZeroTier LLM Proxy

Role-aware setup repo for sharing one Windows-hosted `llama.cpp` model with Linux/macOS Codex clients over ZeroTier.

The implementation source of truth is [`zerotier-llm-bootstrap.html`](zerotier-llm-bootstrap.html). This repo turns that guide into copy-pasteable config and small verification scripts.

## Roles

- **Client agents, Linux/macOS:** use `config/codex.client.toml.example` and `scripts/unix/*`.
- **Server agent, Windows:** own the LiteLLM and `llama.cpp` host setup. See `docs/server-agent-handoff.md`; server scripts/config should live under `scripts/windows/` and `config/server/` when added.

## Current Client Defaults

- Windows ZeroTier host: `10.88.140.94`
- LiteLLM proxy: `http://10.88.140.94:4000/v1`
- Model name: `qwen36-turbo-hermes`
- Client auth token: placeholder value; LiteLLM/llama.cpp only require a non-empty key for this setup.

## Linux Client Quick Start

```bash
./scripts/unix/install-codex-client-config.sh
./scripts/unix/verify-client.sh
codex exec --model qwen36-turbo-hermes "Say hello"
```

Override defaults when needed:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 \
LLM_API_KEY=local-dev-key \
./scripts/unix/install-codex-client-config.sh
```

## Client Verification Only

From this Linux machine, the verification script checks the server from the outside:

- ZeroTier TCP reachability to `:4000`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

It does not start LiteLLM, install server dependencies, open firewall rules, or touch `llama.cpp`.

