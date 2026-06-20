# Headroom + Hermes Proof Of Concept

## Purpose

Headroom addresses the observed failure mode: Hermes tool outputs and old turns
expanded a Discord request to 54,737 input tokens. The local model itself was
not stalled; the oversized prompt caused queueing and context-dependent decode
slowdown.

This POC puts Headroom between Hermes and LiteLLM:

```text
Hermes -> Headroom :8787 -> LiteLLM :4000 -> llama.cpp :8080
```

The existing model name remains `qwen36-turbo-hermes`. No model-server setting
changes are required.

## Why Headroom

- It provides an OpenAI-compatible proxy, so Hermes can retain
  `transport: chat_completions`.
- It compresses large tool outputs, logs, JSON, and historical content before
  they reach the model.
- Its CCR store preserves originals and its Hermes plugin provides
  `headroom_retrieve` when the model needs a compressed original.
- It runs as a Windows-local service and forwards to the existing Windows
  LiteLLM route.

## Non-Goals

- This does not increase model decode tokens per second.
- This is not a replacement for llama.cpp or LiteLLM.
- Do not enable it against the production Hermes endpoint until the POC proves
  tool-call fidelity and prompt-token reduction on representative Discord work.

## Preparation

The repository includes Windows lifecycle scripts.
Install and start from PowerShell:

```powershell
.\scripts\windows\Install-HeadroomHermes.ps1
.\scripts\windows\Start-HeadroomHermes.ps1
.\scripts\windows\Get-HeadroomHermesStatus.ps1
```

The proxy binds port `8787` and forwards to `http://127.0.0.1:4000/v1`.
It starts with local-only settings, telemetry disabled, a 1,000-token minimum
compression threshold, the most recent 12 turns protected, and the two Hermes
tools that must not be re-compressed excluded.

## Hermes Plugin

Install the Hermes retrieval plugin before routing traffic through Headroom:

```powershell
.\scripts\windows\Install-HermesHeadroomPlugin.ps1
```

It copies the plugin into the Hermes user directory, enables the `headroom`
toolset and `headroom_retrieve` plugin, creates a timestamped config backup,
and restarts the gateway.

## Controlled Cutover

Only after the proxy and retrieval tool pass direct requests, either use the
route script or change the Hermes provider API temporarily from:

```yaml
api: http://172.24.16.1:4000/v1
```

to:

```yaml
api: http://172.24.16.1:8787/v1
```

Run a fresh Discord thread with the same agent workflow, then compare:

- Headroom `/stats` input tokens before and after compression
- llama.cpp prompt tokens and prefill time
- llama.cpp decode tok/s
- Tool-call correctness and any `headroom_retrieve` calls

Rollback is one config line plus a Hermes gateway restart.

The equivalent route commands are:

```powershell
.\scripts\windows\Set-HermesHeadroomRoute.ps1 -Enable
.\scripts\windows\Set-HermesHeadroomRoute.ps1 -Disable
```

## DevSpace Assessment

DevSpace is a self-hosted MCP server that gives ChatGPT access to selected local
workspaces. It can be useful for an interactive ChatGPT coding workflow, but it
does not expose a model API and cannot act as Hermes' Oracle backend. An Oracle
for Hermes still needs an OpenAI-compatible upstream or a purpose-built adapter
such as the existing `webchat2api` lane.
