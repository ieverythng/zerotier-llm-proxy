# Watson stack and Qwen3.8 evaluation

Date: 2026-09-01

## Decision

Keep `Qwen3.8-27B-UD-IQ3_S.gguf` as the live Hermes model. It is the only
tested candidate that completed a coherent 90k-token prompt with a 100096-token
runtime allocation while retaining useful VRAM headroom. It is the best overall
choice for the stated objective (higher context first, then speed and agentic
correctness), even though the higher-bit Q3_K_XL quant is marginally faster at
shorter contexts.

The compatibility alias remains `qwen36-turbo-hermes` so the existing LiteLLM,
Hermes, Discord, and Codex profiles do not need a breaking rename.

## Acceptance criteria

- preserve strict structured-output and Hermes workflow behavior;
- improve the usable context ceiling beyond the former 65k allocation;
- measure prefill, TTFT, decode, VRAM margin, and qualitative behavior;
- run through the full llama.cpp → LiteLLM → Headroom → Hermes path;
- use the Unsloth non-thinking defaults in the serving command;
- verify Headroom memory save/search behavior without leaving test facts behind;
- leave all local services healthy and the selected model active.

## Model and runtime

| Item | Value |
|---|---|
| GGUF | `D:\MODELS\Qwen3.8-27B-UD-IQ3_S.gguf` |
| SHA-256 | `d847e2c1e4aa276e4b7b8e9ad7628050e61e165d49ab995407bc36677a6f3864` |
| Alternative checked | `D:\MODELS\Qwen3.8-27B-UD-Q3_K_XL.gguf` (`8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e`) |
| llama.cpp | official CUDA 13.3 Windows build `0.3.0-dev`, build 10621, commit `c1d0e7a00` |
| context | `-c 100000` → server reports `n_ctx=100096`; training context `262144` |
| KV cache | `-ctk q8_0 -ctv q8_0` |
| batching | `-b 512 -ub 256` |
| attention / reasoning | `-fa on`, reasoning off |
| non-thinking sampling | temperature `0.7`, top-p `0.8`, top-k `20`, min-p `0`, presence penalty `1.5`, repeat penalty `1` |
| service ports | llama.cpp `8080`, LiteLLM `4000`, Headroom `8787` |

The active command line is recorded by `Get-CimInstance Win32_Process`; it
loads the IQ3_S file with the parameters above and binds `0.0.0.0:8080`.

## Benchmark comparison

| Measure | Watson Qwen3.6 Q3_K_M | Qwen3.8 Q3_K_XL | Qwen3.8 IQ3_S | Winner / interpretation |
|---|---:|---:|---:|---|
| 42k prefill | 1,176.960 tok/s | 1,316.577 tok/s | 1,287.914 tok/s | Q3_K_XL short-context speed |
| 42k TTFT | 35.318 s | 31.660 s | 32.392 s | Q3_K_XL, IQ3_S close |
| Forced 512 decode mean | 43.172 tok/s | 49.908 tok/s | 48.905 tok/s | Q3_K_XL by a small margin |
| Qualitative suite | 4/5 | 3/5 | 4/5 | Watson and IQ3_S tie |
| Routed workflow | 4/4 | 3/4 direct | 4/4 via Headroom | IQ3_S / Watson |
| Minimum VRAM at 42k | 211 MiB | 570 MiB | 1,951 MiB | IQ3_S safety margin |

The IQ3_S is 12.03 GB on disk and therefore gives materially more KV-cache
headroom than Q3_K_XL on this 16 GB card. Its one qualitative miss is the same
Discord-style verbosity constraint that Watson also misses; its context,
uncertainty, JSON, and workflow cases pass.

## Context sweep

| Allocation / prompt | Q3_K_XL prefill | Q3_K_XL min free | IQ3_S prefill | IQ3_S min free | Result |
|---|---:|---:|---:|---:|---|
| 70k / 68,968 | 1,094.205 tok/s | 280 MiB | 1,078.131 tok/s | 1,402 MiB | both coherent |
| 80k / 78,822 | 950.607 tok/s | 127 MiB | 1,012.976 tok/s | 1,002 MiB | both coherent; Q3_K_XL near the wall |
| 100k / 88,650 | not safely completed | near-zero during the cliff | 965.075 tok/s | 614 MiB | IQ3_S completes coherently |

The Q3_K_XL 100k run was stopped after the prefill collapsed to roughly
34 tok/s at 17,408 processed tokens. The IQ3_S 100k run completed a 88,650-token
prompt in 91.86 seconds of prefill and retained the expected sentinel. This is
why IQ3_S is the production selection despite Q3_K_XL's better 42k decode.

## Full routed workflow

`Test-Qwen36HermesWorkflowGate.ps1` was run against
`http://127.0.0.1:8787/v1` after the package and routing updates. All four cases
passed:

1. exact response marker;
2. strict JSON tool selection;
3. 12k synthetic long-history sentinel retention;
4. continuation JSON.

The post-update Headroom run took 29.544 seconds total and reported cache hits
on the tool, long-history, and continuation cases. Headroom health stayed
`healthy`/`ready` with Rust core loaded, local memory initialized, and the
LiteLLM upstream reachable.

## Headroom and persistent memory

Headroom was upgraded to `0.37.0` and LiteLLM to `1.99.0`. The launcher now
starts:

```text
headroom proxy ... --memory --memory-storage global
```

The explicit global mode is intentional for this single-user machine: Hermes,
Discord, and local Codex share one SQLite store while `x-headroom-user-id:
juanbeck` keeps the user partition stable. The launcher also sets
`HEADROOM_ROLLOUT_CHANNEL=canary` because v0.37 gates
`--intercept-tool-results` behind that channel.

A disposable live test through `/v1/responses` forced `memory_save`, then forced
`memory_search` for the exact fact and received `FOUND`; the record was deleted
afterward. The round-trip is captured in
`qwen38-iq3s-headroom-memory-roundtrip.json`. The store contains five retained
non-probe USER memories after cleanup. `headroom_retrieve` remains a separate
CCR hash-recovery tool, not semantic long-term memory.

The test also exposed a v0.37 implementation seam: the Responses handler calls
`_execute_memory_tool` without passing its request context, so project-mode
saves can fall back to the legacy global backend. Making global mode explicit
keeps save and search on the same backend until that upstream seam is fixed.

The live `/stats` snapshot after the final workflow recorded 15 API requests,
14 compressed requests, 2,478 input tokens removed, and zero failures. Its
request tags include `project-id=watson-global`, `user-id=juanbeck`, and
`memory_injected=true` on the memory-search turn. CCR retrievals were zero in
this run because no test response produced a retrievable compressed marker;
the Hermes `headroom_retrieve` plugin itself is enabled and points at
`/v1/retrieve`.

## Codex compression and login

`codex login status` reports `Logged in using ChatGPT` on `codex-cli 0.146.0`,
but the local ID-token metadata is expired. This explains why a long-running
conversation may fail while compressing even though the CLI still reports the
ChatGPT auth mode. Run `codex login` in PowerShell and complete the browser
sign-in to refresh the session; no logout is required.

There is no `compact` subcommand and no local Codex setting that exposes a
percentage threshold. The 33%-remaining preference therefore cannot be wired
into this desktop client's internal compaction trigger from this repository.
The OpenAI Responses API does expose a `context-management` compaction control
for API clients, but Codex desktop owns that request path. The local IQ3_S model
can generate a compact JSON state summary through LiteLLM (validated in
`qwen38-iq3s-local-compression-probe.json`), which is a useful manual fallback,
not a drop-in replacement for Codex's opaque compaction items.

## Live final state

The final health sweep showed:

- llama.cpp model alias `qwen36-turbo-hermes`, `n_ctx=100096`, IQ3_S quant;
- LiteLLM `/v1/models` healthy on port 4000;
- Headroom `0.37.0` healthy/ready on port 8787, memory enabled;
- Hermes gateway active with systemd linger enabled and routed through Headroom;
- approximately 728 MiB free VRAM while idle at the 100k allocation.

The Codex profile at `C:\Users\Admin\.codex\qwen36-zerotier.config.toml` now
advertises `model_context_window = 100096`, matching the live server rather
than the old 65k value.

Residual risks are the IQ3_S quality/quantization trade-off, low (but positive)
VRAM margin at 100k under load, and the canary requirement for Headroom's tool
result interceptor. The 100k profile is therefore the selected research/
production setting, but not a claim that every workload will remain responsive
at the full ceiling.

## Artifacts

Raw measurements, health snapshots, package freezes, and probes are in
`docs/artifacts/qwen38-evaluation-2026-09-01/`; see its README for the map.

External implementation references:

- [Qwen3.8 repository](https://github.com/QwenLM/Qwen3.8)
- [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Unsloth Qwen3.8 GGUFs](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
