# ZeroTier LLM Proxy

Serve local LLM inference over a [ZeroTier](https://www.zerotier.com/) overlay network via an OpenAI-compatible API, with optional GPT-5 Oracle access.

## Architecture at a Glance

```
Windows Host (GPU)                    WSL (Orchestration)
┌──────────────┐                      ┌──────────────┐
│  llama.cpp   │:8080                 │  Hermes      │:8001
│  (Qwen3.6)   │───┐                  │  Gateway     │
└──────────────┘   │                  └──────────────┘
                   ▼
            ┌──────────────┐          ┌──────────────┐
            │  LiteLLM     │:4000    │ webchat2api  │:9000
            │  Proxy       │         │ (GPT-5)      │
            └──────┬───────┘         └──────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  ZeroTier Network    │
        │  10.88.x.x:4000     │
        └──────────────────────┘
```

**Full architecture diagram:** [docs/architecture.html](docs/architecture.html)

## Lucebox/DFlash Pilot

Lucebox/DFlash preserves the ZeroTier/LiteLLM access pattern, but is not the
recommended production backend on the installed 16GB GPU. Its documented
Qwen3.6 target-plus-draft setup requires at least 22GB VRAM. The launchers
therefore block unsafe defaults unless an explicit experimental override is
used.

```powershell
# Experimental low-VRAM profile, 65k context
.\scripts\windows\Start-Lucebox65kStack.ps1

# Experimental maximum-context profile (requires explicit low-VRAM override)
.\scripts\windows\Start-Lucebox128kStack.ps1

# Stop Lucebox/DFlash, compatibility proxy, and LiteLLM
.\scripts\windows\Stop-LuceboxStack.ps1
```

Current endpoints when running:

| Service | ZeroTier |
|---------|----------|
| DFlash compatibility proxy | `http://10.88.140.94:18080/v1` |
| LiteLLM / Hermes | `http://10.88.140.94:4000/v1` |

Current measured recommendation: use llama.cpp TurboQuant with the Qwen3.6
MTP pi-tune model for normal Hermes/Codex work. The stable model name remains
`qwen36-turbo-hermes`; LiteLLM selects the active backend. See the
[benchmark suite](docs/local-llm-benchmark-suite-2026-06-19.md) for measured
prefill, decode, compatibility, and memory findings.

## Context Management POC

[Headroom](docs/headroom-hermes-poc.md) is staged as an optional local proxy
between Hermes and LiteLLM. It targets tool-output and history growth; it is not
enabled by default and does not replace the model backend.
See the [HTML operational guide](docs/headroom-hermes-integration.html) for the
runtime architecture, scripts, cutover, and rollback procedure.

## Quick Start

### Start Everything (One Command)

```powershell
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1
```

This launches:
1. **llama.cpp** — Qwen3.6 TurboQuant Hermes on port 8080
2. **LiteLLM Proxy** — OpenAI-compatible API on port 4000
3. **webchat2api** — GPT-5 Oracle on port 9000 (via WSL)

### Options

```powershell
# Skip Oracle (llama.cpp + LiteLLM only)
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -NoOracle

# Custom model
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 `
  -Model qwopus-3.6-27b `
  -ModelPath "D:\MODELS\Qwopus-VL-3.6-27B-Q3_K_M\Qwopus3.6-27B-v2-Q3_K_M.gguf"

# Skip llama.cpp startup (already running)
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -SkipLlamaStart
```

## Endpoints

| Service | Local | ZeroTier |
|---------|-------|----------|
| llama.cpp | `http://127.0.0.1:8080/v1` | — |
| LiteLLM Proxy | `http://127.0.0.1:4000/v1` | `http://10.88.140.94:4000/v1` |
| webchat2api (Oracle) | `http://127.0.0.1:9000/v1` | — |

## GPT-5 Oracle (webchat2api)

The Oracle provides access to GPT-5 models via a ChatGPT Plus session proxy.

### Available Models
- `gpt-5` — Standard GPT-5
- `gpt-5-5` — GPT-5.5
- `gpt-5-5-thinking` — GPT-5.5 with extended reasoning (default)

### Usage from Hermes

```bash
# Via the ask-gpt5.sh script
bash /home/juanbeck/Watson/scripts/ask-gpt5.sh "Your question" gpt-5-5-thinking
```

### Token Refresh

When OpenAI revokes your access token (you'll see `密钥无效或已失效`):

```powershell
.\scripts\windows\Refresh-ChatGPTToken.ps1 -RestartProxy
```

This script:
1. Attempts to extract your `__Secure-access_token` from Chrome/Edge cookies
2. Updates `webchat2api/data/accounts.json` with the new token
3. Restarts the webchat2api proxy

**Manual fallback:** If automatic extraction fails, the script will prompt you to paste the token from browser DevTools (Application → Cookies → `__Secure-access_token`).

### Oracle Limitations

- **Text-only** — No function calling, no tool use, no image input
- **Rate limited** — ~50 messages per 8-hour window (ChatGPT Plus)
- **Token rotation** — OpenAI periodically revokes access tokens; refresh required
- **ToS risk** — Automated usage of ChatGPT Plus session may violate Terms of Service

For tasks requiring tool calling, use **Codex delegation** instead (configured as the default delegation provider in Hermes).

## Codex Delegation

Hermes Agent uses OpenAI Codex CLI (OAuth) for delegated coding tasks:
- Full function calling and tool use
- Terminal execution capabilities
- No token revocation issues

Configured in `~/.hermes/config.yaml`:
```yaml
delegation:
  model: codex
  provider: openai-codex
```

## Model Swap

To load a different model (e.g., for benchmarking):

```powershell
# Stop current server
.\llama-cpp-server\scripts\stop_llama_server.ps1

# Start with new model
.\llama-cpp-server\scripts\start_turbo_hermes.ps1 `
  -Profile hermes-qwen36-64k `
  -ModelPath "D:\MODELS\path\to\model.gguf"
```

Or via the unified script:
```powershell
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 `
  -Model your-model-name `
  -ModelPath "D:\MODELS\path\to\model.gguf"
```

## Scripts Reference

### Windows Scripts (`scripts/windows/`)

| Script | Purpose |
|--------|---------|
| `Start-Qwen36ZeroTierStack.ps1` | Unified startup: llama.cpp + LiteLLM + Oracle |
| `Start-Lucebox65kStack.ps1` | Lucebox/DFlash + proxy + LiteLLM, recommended 65k context profile |
| `Start-Lucebox128kStack.ps1` | Lucebox/DFlash + proxy + LiteLLM, experimental 128k context profile |
| `Start-LuceboxZeroTierStack.ps1` | Parameterized Lucebox/DFlash stack launcher |
| `Stop-LuceboxStack.ps1` | Stop Lucebox/DFlash, compatibility proxy, and LiteLLM |
| `Benchmark-LocalLlmEndpoint.ps1` | Benchmark OpenAI-compatible local LLM endpoints |
| `Start-Qwen36LiteLLM.ps1` | LiteLLM proxy only |
| `Refresh-ChatGPTToken.ps1` | Extract browser token, update accounts.json |
| `Switch-Qwen36ContextMode.ps1` | Switch between context size profiles |
| `Test-Qwen36ContextMode.ps1` | Test current context configuration |
| `Measure-Qwen36ProxyThroughput.ps1` | Benchmark throughput metrics |
| `Invoke-QwenContextSweep.ps1` | Sweep test across context sizes |
| `Compare-QwenSweepResults.ps1` | Compare benchmark results |

## Project Structure

```
zerotier-llm-proxy/
├── config/server/litellm-config.yaml    # LiteLLM backend config
├── docs/architecture.html               # Full architecture documentation
├── scripts/windows/                     # PowerShell scripts
└── README.md                            # This file
```

## Requirements

- **Windows host** with NVIDIA GPU (CUDA)
- **WSL 2** with Ubuntu
- **Python 3.11+** in WSL (for webchat2api)
- **PowerShell 5+** on Windows
- **ZeroTier** installed and connected to network `3b19b3a716937e29`

## Baseline Performance

Qwen3.6 TurboQuant Hermes 27B (Q3_K_M):

| Context | Throughput | TTFT |
|---------|-----------|------|
| Short (500 tok) | 42.7 tok/s | 0.93s |
| Medium (2K ctx) | 25.9 tok/s | 2.89s |
| Long (8K ctx) | 8.9 tok/s | 8.89s |
