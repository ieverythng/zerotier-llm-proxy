# Watson vs Ternary Bonsai production evaluation

Date: 2026-07-29

## Decision

Keep `Qwen3.6-27B-Q3_K_M.gguf` (Watson) as the Hermes production model.

Ternary Bonsai is the clear performance and high-context winner, but not the
overall agentic winner. It failed the strict workflow correctness gate after the
LiteLLM thinking integration was fixed, while Watson passed the same gate before
and after restoration. Correctness is a hard production constraint for the
Hermes tool-using orchestrator, so speed cannot compensate for this failure.

Keep Bonsai as an opt-in research/long-document worker. Its 100k result is real
and useful, but its measured time to first token is still too high for the
default interactive Discord route.

## Scorecard

| Measure | Watson Q3_K_M | Ternary Bonsai Q2_0 | Result |
|---|---:|---:|---|
| GGUF size | 12.653 GiB | 6.673 GiB | Bonsai is 47.3% smaller |
| 42k prefill | 1,238.551 tok/s | 1,369.038 tok/s | Bonsai +10.5% |
| 42k TTFT | 33.546 s | 30.333 s | Bonsai 9.6% faster |
| Forced 512 decode, two-run mean | 44.568 tok/s | 70.871 tok/s | Bonsai +59.0% |
| Qualitative suite | 4/5 | 4/5 | Count tied; Bonsai gave weaker tuning advice |
| Routed Hermes workflow | 4/4 | 3/4 | Watson wins hard gate |
| 80k forced prefill | not promoted | 1,159.808 tok/s | Bonsai completed; 68.121 s TTFT |
| 100k forced prefill | not feasible in this profile | 1,056.675 tok/s | Bonsai completed; 93.468 s TTFT |
| 131k VRAM safety | not tested | 156 MiB idle free | Bonsai rejected before load testing |

The two Bonsai decode repeats were 67.389 and 74.352 tok/s. Watson's were 44.405
and 44.731 tok/s. Bonsai retained the required sentinel at about 92.6k input
tokens, but wrapped the continuation JSON in a Markdown fence at both 12k and
90k. Watson returned strict JSON.

## Runtime and integration findings

The downloaded GGUF uses group-128 `Q2_0`. The existing TurboQuant build cannot
load that tensor layout. The PrismML llama.cpp fork at
`7529fdaaf99ffdc5ca71ace9c7409a56b27ad92f` loaded and benchmarked it correctly.

Bonsai's default chat template emits hidden reasoning. A top-level
`chat_template_kwargs` field is discarded by LiteLLM 1.87.0. The correct route
is:

```json
{
  "extra_body": {
    "chat_template_kwargs": {
      "enable_thinking": false
    }
  }
}
```

After fixing the benchmark harness to use that route, Bonsai improved from 1/4
to 3/4 on the full routed workflow. The remaining failure is therefore model
instruction adherence, not a proxy wiring error.

## High-context interpretation

Bonsai makes a 100k-class worker practical on this 16 GiB GPU, with about
1.9 GiB sampled free VRAM at 98,504 input tokens. However, a 93.468-second TTFT
does not meet an interactive Discord target. The next bounded high-context
challenger should combine Bonsai with a validated 4-bit KV cache and measure
quality as a hard gate. DSpark should be evaluated separately before combining
it with 100k context because the extra draft model competes for the same VRAM.

### Follow-on Q4_0 KV-cache cycle

A bounded follow-on cycle tested Prism's experimental 4-bit KV cache using
`-fa on -ctk q4_0 -ctv q4_0`. No model-specific mean-centering bias was
available, so correctness remained a hard gate and the results should not be
read as quality equivalence.

| Measure | FP16 KV | Q4_0 KV | Delta |
|---|---:|---:|---:|
| 98.5k prefill | 1,056.675 tok/s | 1,042.139 tok/s | -1.4% |
| 98.5k TTFT | 93.468 s | 94.705 s | +1.3% |
| Short-tail decode | 48.043 tok/s | 40.762 tok/s | -15.2% |
| Minimum free VRAM | 1,906 MiB | 6,203 MiB | +4,297 MiB |

The 131,072 allocation that previously left only 156 MiB idle free VRAM became
safe with KV4:

- 123,100 cold prompt tokens completed at 921.131 tok/s;
- TTFT was 133.861 seconds;
- minimum sampled free VRAM was 5,558 MiB;
- the routed gate retained the exact sentinel/action at 123,424 input tokens;
- the long retention case took 135.272 seconds;
- the overall workflow remained 3/4 because continuation JSON was fenced.

This proves that a useful 120k-class Bonsai recovery/batch worker fits the GPU.
It does not change the production selection: latency is not interactive, the
uncalibrated cache has a documented accuracy caveat, and the same strict-output
failure remains.

DSpark was not run in this cycle. The matching drafter GGUF is not downloaded,
and Prism documents that its current server path disables cross-request prompt
cache reuse and re-prefills every request. That trade-off conflicts with this
cycle's long-context prefill objective. It remains a separate short-context
decode experiment.

The 1-bit Bonsai variant is better framed as a future swarm worker than the main
orchestrator. The model author's own published table places the ternary model
above the 1-bit model on average, and also shows lower instruction-following and
agentic scores for ternary Bonsai than the FP16 base.

## Hermes context display and Headroom

The previously observed 128k display was metadata fallback, not the active
server allocation. Headroom's proxied model listing did not expose llama.cpp's
`meta.n_ctx`, so Hermes fuzzy-matched `qwen` to its 131,072-token catalog rule.
The explicit `model.context_length: 65536` override now resolves correctly.

The live values after restoration are:

- llama.cpp: 65,536
- Codex profile: 65,536
- Hermes runtime override: 65,536
- model training context reported by GGUF: 262,144

Headroom 0.26.0 is healthy and its local memory backend is initialized. Hermes
now sends stable `x-headroom-project-id: watson-global` and
`x-headroom-user-id: juanbeck` headers, so any Headroom memories saved into that
project are globally addressable across Hermes surfaces. The store currently
contains zero durable memories. `headroom_retrieve` is not semantic long-term
memory: it retrieves compressed tool-output content by hash. Hermes' built-in
memory tool still writes its own `~/.hermes/memories/USER.md`.

## Final production state

The stack was restored through the canonical launcher with:

- model: `D:\MODELS\Qwen3.6-27B-Q3_K_M.gguf`
- context: 65,536
- batch / microbatch: 512 / 256
- KV cache: `q8_0` K and `turbo2` V
- reasoning: off
- ports: llama.cpp 8080, LiteLLM 4000, Headroom 8787
- Hermes gateway: active and routed to Headroom

The final four-case routed workflow passed 4/4, and the server, proxy, Codex
profile, and Hermes metadata all resolved to the intended production state.

## Artifacts

Raw measurements and outputs are in
`docs/artifacts/bonsai-evaluation-2026-07-25/`. The compact experiment ledger is
`results.csv`.

External implementation references:

- PrismML Ternary Bonsai model card:
  https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf
- PrismML Bonsai demo and runtime instructions:
  https://github.com/PrismML-Eng/Bonsai-demo
- PrismML 4-bit KV-cache guidance:
  https://github.com/PrismML-Eng/Bonsai-demo/blob/main/KV-CACHE.md
- PrismML speculative-decoding guidance:
  https://github.com/PrismML-Eng/Bonsai-demo/blob/main/SPECULATIVE.md
- llama.cpp upstream support discussion:
  https://github.com/ggml-org/llama.cpp/discussions/22019
