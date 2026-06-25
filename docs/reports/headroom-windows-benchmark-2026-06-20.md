# Headroom Windows Benchmark - 2026-06-20

## Decision

The proxy-only benchmark did not justify a cutover. A real Hermes integration
trial is now active:

```text
Hermes (WSL) -> Headroom (Windows :8787) -> LiteLLM (Windows :4000) -> llama.cpp (Windows :8080)
```

Headroom is a Windows-local OpenAI-compatible proxy on port 8787. The
Headroom retrieval plugin is enabled in Hermes and the route script created
timestamped configuration backups before the cutover.

## Tested Windows Topology

```text
Hermes (WSL) -> Windows host bridge -> Headroom :8787 -> LiteLLM :4000 -> llama.cpp :8080
```

The proxy exposes the existing aliases, including `qwen36-turbo-hermes`.
The intended one-command startup remains:

```powershell
.\scripts\windows\Start-Qwen36HeadroomStack.ps1
```

The normal launcher remains `Start-Qwen36ZeroTierStack.ps1`.

## Measurements

All requests targeted the currently running original llama.cpp and LiteLLM
backend. The benchmark used a synthetic tool-result history and requested a
short answer.

| Run | Prompt tokens observed upstream | Completion tokens | Direct time | Headroom time | Tokens removed |
|---|---:|---:|---:|---:|---:|
| Protected history | 4,220 | 14 | 3.326 s | 45.369 s | 0 |
| Eligible older history | 6,298 | 14 | 2.963 s | 14.289 s | 0 |
| Experimental forced profile | 3,298 | 14 | 2.813 s | 16.099 s | 0 |

The prior llama.cpp MTP benchmark remains the relevant decode baseline:
54.12 tok/s at a 17.4k-token prompt. These Headroom tests are not a model
throughput regression; they show proxy preprocessing overhead and no
compression for this OpenAI chat-completions message shape.

## Findings

- The live Discord task completed six tool turns through the active Hermes
  route. Headroom compressed six requests and removed 56,709 tokens in total.
- The best observed pass was 60,566 -> 39,189 input tokens, a 35.3% reduction.
  The final 75,154-token pass was reduced to 53,777 tokens.
- The initial compression pass took 5.36 seconds, but cache-hit compression
  work on repeated context fell to approximately 39-130 ms.
- The default profile protected recent content and did not compress the test
  tool result.
- The older-history run recorded `prefix_frozen`, passed the full prompt
  upstream, and added approximately 11.3 seconds.
- The experimental startup with zero protected recent turns still reported
  `force_kompress: false` at runtime and removed zero tokens.
- The proxy required a tokenizer cold start on its first request. Even after
  warm-up, its added latency outweighed any benefit because it removed no
  tokens.
- The real Hermes CLI trial sent 20,181 prompt tokens through Headroom and
  confirmed the route and plugin loading. The local model did not call the
  requested terminal tool and returned unrelated text, so that turn supplied
  no tool-result payload for the proxy to compress.
- A minimal routed Hermes request asking for an exact `PONG` response also
  returned a generic greeting. Treat this as a current Hermes/local-model
  instruction-following issue; it is not evidence of compression corruption
  because no compression transform was applied on either request.

## Scripts

- `Install-HeadroomHermes.ps1`: isolated Windows virtual environment.
- `Start-HeadroomHermes.ps1`: Windows Headroom service.
- `Stop-HeadroomHermes.ps1` and `Get-HeadroomHermesStatus.ps1`: lifecycle.
- `Benchmark-HeadroomProxy.ps1`: direct versus proxy tool-history test with
  Headroom `/stats` capture.
- `Start-Qwen36HeadroomStack.ps1`: one-command stack launcher, optional
  explicit Hermes route cutover.
- `Set-HermesHeadroomRoute.ps1`: explicit, backed-up Hermes provider switch.

## Revisit Criteria

Run a real multi-tool Discord task while the active route is in place. A
successful test must show both material reduction in upstream prompt tokens and
preserved tool-call behavior. Compare wall time, llama.cpp prefill time, decode
tok/s, Headroom statistics, and LiteLLM latency. Roll back with
`Set-HermesHeadroomRoute.ps1 -Disable` if the test regresses correctness.
