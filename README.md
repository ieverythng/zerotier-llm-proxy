# ZeroTier LLM Proxy

Role-aware setup repo for sharing one Windows-hosted `llama.cpp` model with Linux/macOS Codex clients over ZeroTier.

The implementation source of truth is [`zerotier-llm-bootstrap.html`](zerotier-llm-bootstrap.html). This repo turns that guide into copy-pasteable config and small verification scripts.

## Roles

- **Client agents, Linux/macOS:** use `config/codex.client.toml.example` and `scripts/unix/*`.
- **Server agent, Windows:** own the LiteLLM and `llama.cpp` host setup. See [`docs/server-agent-handoff.md`](docs/server-agent-handoff.md); server scripts/config should live under `scripts/windows/` and `config/server/` when added.

## Current Client Values

- Windows ZeroTier host: `10.88.140.94`
- LiteLLM proxy: `http://10.88.140.94:4000/v1`
- Model name: `qwen36-turbo-hermes`
- Client auth token: none; access is scoped by ZeroTier and Windows Firewall.
- Codex registry: provider is added to `~/.codex/config.toml` as `[model_providers.qwen36-zerotier]`, without changing the global default.
- Codex CLI selection: profile is installed as `~/.codex/qwen36-zerotier.config.toml`.
- Codex model list: a merged catalog is installed at `~/.codex/model-catalogs/qwen36-plus-bundled.json`.

## Codex `wire_api`

Codex custom providers must use `wire_api = "responses"` (Codex 0.136 rejects
`chat_completions`). LiteLLM on the Windows host translates `/v1/responses` to
llama.cpp chat completions. Use `verify-client.sh` to exercise both HTTP paths;
only `responses` is valid in `~/.codex/config.toml`.

## Architecture

Codex routes models to providers inside its compiled Rust binary. There is no per-model provider override in config or model catalogs. The only way to route a specific model through a custom provider is via the `--profile` flag. For the Codex Desktop app (which does not pass `--profile`), a shell wrapper at `~/.local/bin/codex` intercepts invocations with `-m qwen36-turbo-hermes` and auto-injects `-p qwen36-zerotier`.

## Linux Client Quick Start

```bash
# Install provider, profile, and model catalog:
./scripts/unix/install-codex-client-config.sh

# Verify connectivity:
./scripts/unix/verify-client.sh

# CLI usage (requires --profile):
codex exec --profile qwen36-zerotier "Say hello"
```

For the Codex Desktop app, install the shell wrapper so `qwen36-turbo-hermes` is selectable from the model dropdown. See [`docs/client-agent-runbook.md`](docs/client-agent-runbook.md) and the bootstrap HTML file for wrapper setup details.

Override defaults when needed:

```bash
LLM_PROXY_BASE_URL=http://10.88.140.94:4000/v1 \
./scripts/unix/install-codex-client-config.sh
```

When the Windows server is intentionally running a larger context mode, reinstall the client profile with a matching window:

```bash
LLM_CONTEXT_WINDOW=98304 ./scripts/unix/install-codex-client-config.sh
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

To start the whole Windows stack from one command, including the existing `llama.cpp` turbo Hermes launcher when needed:

```powershell
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1
```

The stack launcher forwards Hermes tuning knobs to `C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_turbo_hermes.ps1`:

```powershell
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -Profile hermes-qwen36-64k -ContextSize 65536 -Metrics
```

Measure proxy throughput across short and long synthetic contexts:

```powershell
.\scripts\windows\Measure-Qwen36ProxyThroughput.ps1 -ContextTokens 0,8192,32768,65536
```

Run a backend restart sweep that records throughput and VRAM, then restores the default 65k server:

```powershell
.\scripts\windows\Invoke-QwenContextSweep.ps1 -ServerContextSizes 65536,98304 -PromptContextTokens 0,8192,32768,65536
```

Run a KV-cache and batch sweep at a fixed context size:

```powershell
.\scripts\windows\Invoke-QwenKvCacheSweep.ps1 -ContextSize 65536 -CacheTypeV turbo2,turbo3,turbo4,q8_0 -PromptContextTokens 0,8192
```

Rank all collected sweep results:

```powershell
.\scripts\windows\Compare-QwenSweepResults.ps1
```

Measured on this RTX 5070 Ti host, `65536` is the practical default. `98304` and `131072` can start, but they heavily trade throughput and VRAM headroom for context:

| llama ctx | Status | Notes |
|---:|---|---|
| `65536` | Default | About 14.9 GiB VRAM used at idle, ~1.1 GiB free, usable Hermes latency. |
| `98304` | Special large-context mode | Starts successfully; a ~98k synthetic context took about 152s for a short response. |
| `131072` | Stress mode | Starts successfully and nearly fills VRAM; expect queueing or long stalls under full-context requests. |

For Hermes/Discord sessions, keep a compact external session ledger in the repo or task workspace and paste only the current working set plus the ledger summary after compaction. Treat the huge context modes as recovery or audit tools, not as the normal endpoint setting.

Use [`docs/session-ledger.md`](docs/session-ledger.md) and `scripts/windows/Update-QwenSessionLedger.ps1` to maintain that compact state outside the model request.

To add the selectable profile to this Windows Codex install without changing the default model:

```powershell
.\scripts\windows\Install-CodexQwen36Config.ps1
```

For a deliberate large-context run, match the installed Codex profile to the server context:

```powershell
.\scripts\windows\Install-CodexQwen36Config.ps1 -ContextWindow 98304
```

Restart Codex Desktop after installing the provider so the model/provider registry is reloaded.

## npm Update Warning

Running `npm update -g @openai/codex` overwrites the shell wrapper at `~/.local/bin/codex`. Restore it with:

```bash
~/.codex/restore-codex-wrapper.sh
```
