# Lucebox ZeroTier Current Runbook

Last updated: 2026-06-19

> **Superseded for production use.** The 16GB RTX 5070 Ti cannot run the
> documented DFlash Qwen3.6 target-plus-draft configuration with reliable VRAM
> headroom. Do not use this runbook's earlier `skip-park` recommendations.
> See [the benchmark suite](local-llm-benchmark-suite-2026-06-19.md) for the
> current llama.cpp MTP recommendation and the guarded experimental DFlash path.

This repo now has a Lucebox/DFlash stack that can replace the prior
llama.cpp listener on the ZeroTier network while keeping LiteLLM in front for
Hermes and OpenAI-compatible clients.

## Architecture

```text
Hermes / Codex client
  -> ZeroTier 10.88.140.94:4000
  -> LiteLLM
  -> 127.0.0.1:18080
  -> DFlash compatibility proxy
  -> 127.0.0.1:8080
  -> dflash_server
  -> RTX 5070 Ti
```

Direct proxy access is also available when the stack is running:

- DFlash native server: `http://10.88.140.94:8080/v1`
- DFlash compatibility proxy: `http://10.88.140.94:18080/v1`
- LiteLLM/Hermes endpoint: `http://10.88.140.94:4000/v1`

Use LiteLLM for Hermes. Use the proxy directly for isolated benchmarking or
clients that do not need LiteLLM routing.

## Stop The Stack

```powershell
.\scripts\windows\Stop-LuceboxStack.ps1
```

This stops `dflash_server`, the DFlash compatibility proxy, and LiteLLM on the
default ports.

## Launch Profiles

### Recommended 65k Profile

```powershell
.\scripts\windows\Start-Lucebox65kStack.ps1
```

This starts:

- `--max-ctx 65536`
- `--cache-type-k tq3_0`
- `--cache-type-v tq3_0`
- `--kvflash 4096`
- `--prefill-compression auto`
- `--prefill-threshold 4096`
- `--prefill-keep-ratio 0.10`
- `--prefill-skip-park`
- `DFLASH_FP_USE_BSA=1`
- `DFLASH_FP_ALPHA=0.85`
- DDTREE speculative decoding with budget 22
- `--fa-window 0`

This is the best measured Lucebox profile so far.

### Experimental 128k Profile

```powershell
.\scripts\windows\Start-Lucebox128kStack.ps1
```

This starts the same stack with `--max-ctx 131072`.

128k is confirmed to start and report `context_length=131072`, but it is slower
than the 65k profile on the same long-prompt benchmark. Use it when testing
maximum context capacity, not when prioritizing throughput.

## Endpoints

Check stack health:

```powershell
Invoke-RestMethod http://10.88.140.94:18080/v1/models
Invoke-RestMethod http://10.88.140.94:4000/v1/models
```

Chat completions:

```powershell
$body = @{
  model = "qwen36-turbo-hermes-spec"
  messages = @(@{ role = "user"; content = "Hey" })
  max_tokens = 64
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Uri http://10.88.140.94:4000/v1/chat/completions `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer local-qwen36" } `
  -Body $body
```

Text completions:

```powershell
$body = @{
  model = "qwen36-turbo-hermes-spec"
  prompt = "Say hello in exactly five words."
  max_tokens = 64
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri http://10.88.140.94:18080/v1/completions `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer local-qwen36" } `
  -Body $body
```

## Benchmarking

Use the local benchmark script:

```powershell
.\scripts\windows\Benchmark-LocalLlmEndpoint.ps1 `
  -BaseUrl "http://10.88.140.94:18080/v1" `
  -LongPromptRepeats 620 `
  -MaxTokens 64 `
  -OutFile "_tmp\lucebox-stack\benchmark.json"
```

The benchmark checks:

- `/v1/models`
- `/v1/chat/completions`
- `/v1/completions`
- a long structured chat prompt

## Measured Results

These are wall-clock results measured over ZeroTier against the compatibility
proxy unless noted otherwise.

| Profile | Context | Prompt tokens | Output tokens | Wall time | Effective prompt tok/s | Output tok/s by wall clock | DFlash decode tok/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| PFlash skip-park | 65k | 17,397 | 26 | 35.189s | 494.39 | 0.74 | 18.97 |
| PFlash skip-park | 128k | 17,397 | 26 | 84.326s | 206.31 | 0.31 | 6.46 |
| PFlash no skip-park | 65k | 18,637 | 19 | 185-206s | 90-101 | 0.09-0.10 | 4.3-15.8 |

Short request results:

| Profile | Endpoint | Prompt tokens | Output tokens | Wall time |
|---|---|---:|---:|---:|
| 65k PFlash skip-park | `:18080/chat/completions` | 20 | 10 | 0.668s |
| 65k PFlash skip-park | `:18080/completions` | 19 | 8 | 0.545s |
| 128k PFlash skip-park | `:18080/chat/completions` | 20 | 10 | 2.161s |
| 128k PFlash skip-park | `:18080/completions` | 19 | 8 | 1.781s |

The earlier `0.15 completion tok/s` reading was misleading because it divided a
tiny number of generated tokens by the entire long request wall time. DFlash's
internal decode speed during the best 65k PFlash run was about 19 tok/s for that
long case, and 40-45 tok/s on some 6.3k Hermes-style completions after PFlash.
End-to-end latency is still dominated by prefill/compression and request
queueing, not just decode throughput.

## Hermes Caveat

Hermes/Discord sent repeated 6.3k-token requests with a high output cap while
testing. DFlash handles requests serially, so a small `Hey` can sit behind a
long prompt and appear hung even when short prompts are fast in isolation.

The compatibility proxy caps large `max_tokens` requests to 1024 by default via
`--max-output-tokens 1024`, but queueing can still happen. If Hermes feels stuck,
check the DFlash log:

```powershell
Get-Content _tmp\lucebox-stack\dflash.combined.log -Tail 120
```

Look for active `chat START`, `compress`, and `chat DONE` lines.

## Current Recommendation

Use the 65k wrapper for day-to-day Hermes/Codex work:

```powershell
.\scripts\windows\Start-Lucebox65kStack.ps1
```

Use the 128k wrapper only when explicitly testing maximum-context behavior:

```powershell
.\scripts\windows\Start-Lucebox128kStack.ps1
```

The 128k profile works, but the measured throughput does not justify making it
the default yet.
