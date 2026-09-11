# Hermes/Discord observability and local compaction

Date: 2026-09-01

## Applied configuration

The active Hermes configuration now uses the public model name `qwen3.8`.
llama.cpp serves that canonical alias directly, while LiteLLM maps the
historical `qwen36-turbo-hermes` names to the same loaded Qwen3.8 IQ3_S GGUF.
This preserves existing clients while making the catalog and new Hermes
sessions truthful.

The context length is read from `http://127.0.0.1:8080/v1/models` rather than
hard-coded. The live metadata reports `n_ctx=100096` (training context
`262144`), and both the Windows and WSL Codex profiles/catalogs now advertise
`100096` for `qwen3.8`.

Hermes compression is configured at `compression.threshold=0.80`. Hermes first
subtracts `model.max_tokens=16384` and applies a 65,536-token safety floor, so
this is an effective trigger of `66,969` input tokens for the current runtime:
approximately 67% of the full 100,096-token window, leaving the requested 33%
remaining. A ratio of `0.67` would not have changed the effective trigger because
of that floor.

The Codex primary compression route remains `openai-codex` when its OAuth
session is healthy. Its per-task fallback is now a supported
`auxiliary.compression.fallback_chain` entry to the local `qwen3.8` LiteLLM
endpoint. The former `auxiliary.compression.fallback_model` key was stale and
has been removed; Hermes confirmed the fallback client resolves successfully.

## Discord controls

Discord streaming is enabled at both the gateway and Discord platform levels.
The Discord runtime footer is enabled with `model`, `context_pct`, and `latency`,
so each final response can carry compact run metadata without changing the CLI
footer. The existing Hermes commands provide the rest of the useful telemetry:

- `/context` shows the live used/total gauge, effective compression threshold,
  compression savings, and session totals.
- `/status` shows the selected model/provider and current context usage.
- `/usage` shows token accounting and throughput-oriented usage data.

llama.cpp also exposes Prometheus-style `/metrics`, including current prompt
and predicted-token throughput (`llamacpp:prompt_tokens_seconds` and
`llamacpp:predicted_tokens_seconds`). Those counters are not currently copied
into Hermes's footer. A future Discord status card can poll `/metrics` and
`/slots` every few seconds and publish an edited embed; this is preferable to
putting a rapidly changing tok/s value into every assistant message.

## Verification

- LiteLLM `/v1/models` exposes `qwen3.8` plus all three compatibility aliases;
  llama.cpp exposes `qwen3.8` with `meta.n_ctx=100096`.
- A non-streaming `qwen3.8` request through Headroom returned the exact marker
  `QWEN38_READY`.
- A streaming `qwen3.8` request through Headroom returned SSE chunks and a
  final usage block.
- Hermes `config check` passed and `hermes-gateway.service` is active.
- Headroom `/health` is healthy/ready with memory initialized and global
  storage enabled.
- The Codex catalog entry is
  `C:\\Users\\Admin\\.codex\\model-catalogs\\qwen38-plus-bundled.json`;
  the WSL mirror is installed under `/home/juanbeck/.codex/model-catalogs/`.

## Maintenance commands

```powershell
# Re-read live llama.cpp metadata and apply Hermes settings.
.\scripts\windows\Sync-HermesQwen38Metadata.ps1

# Regenerate the Windows catalog/profile after a context-size change.
.\scripts\windows\Install-CodexQwen38Catalog.ps1

# The normal stack launcher now defaults to the live 100096 allocation.
.\scripts\windows\Start-WatsonStack.ps1
```
