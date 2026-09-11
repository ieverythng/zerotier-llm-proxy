# Qwen3.8 autoresearch resume plan

This is the post-recovery continuation of the Watson stack optimization loop.
The canonical runtime is:

`C:\Users\Admin\PROJECTS\llama-b10621-win-cuda133`

The production candidate is `D:\MODELS\Qwen3.8-27B-UD-IQ3_S.gguf`, served as
`qwen3.8` with `-c 100096`, `-b 512`, `-ub 256`, `-ctk q8_0`, `-ctv q8_0`,
`--fit off`, and flash attention enabled.

## Existing evidence

The 2026-09-01 cold runs are the baseline to reproduce after the GPU reset:

| Prompt | Prefill | Decode | First token | Minimum free VRAM |
| ---: | ---: | ---: | ---: | ---: |
| 41,382 tokens (IQ3_S) | 1,287.9 tok/s | 40.0 tok/s | 32.39 s | 1,951 MiB |
| 68,968 tokens (IQ3_S) | 1,078.1 tok/s | 32.7 tok/s | 64.10 s | 1,402 MiB |
| 78,822 tokens (IQ3_S) | 1,013.0 tok/s | 27.5 tok/s | 78.02 s | 1,002 MiB |
| 88,650 tokens (IQ3_S) | 965.1 tok/s | 26.4 tok/s | 92.02 s | 614 MiB |

The Q3_KXL comparison reached only 127 MiB minimum free VRAM at 78,822
tokens, so it is not the default 100k candidate without a measured memory
improvement.

## Ordered run

1. Reboot/reset the GPU, then run `Start-WatsonStack.ps1` and require all
   local, WSL, LiteLLM, Headroom, and Hermes route gates to pass.
2. Reproduce the 41k IQ3_S stream and 512-token decode controls twice. Record
   exact command lines, build hash, prompt/cache counts, TTFT, prefill, decode,
   and sampled VRAM.
3. Run the context staircase at 65,536, 81,920, 98,304, and 100,096. Stop on
   the first health loss, invalid tool output, or minimum-free-VRAM breach.
4. For the highest safe context, ablate one variable at a time: batch/ubatch,
   K/V cache type, and flash-attention mode. Keep a challenger only after two
   cold repetitions and a real Hermes tool workflow.
5. Run the route gate through LiteLLM and Headroom, then one short Discord
   canary. A provider timeout or missing first-visible output rejects the
   challenger regardless of raw tok/s.
6. Restore the promoted profile and verify the declared context and model alias
   after every experiment. Never leave an experimental server as the default.

## Promotion criteria

- two cold runs improve the matched baseline;
- TTFT, prefill, and decode are recorded separately;
- strict tool JSON, long-history retention, and Headroom memory round-trip pass;
- no OOM, CUDA/driver error, or unhealthy listener;
- retain at least 512 MiB sampled free VRAM at the promoted context;
- the final restore and Hermes/Discord route checks pass.

Synthetic throughput is evidence for ranking, not sufficient evidence for
promotion.

## First post-reboot control results (2026-09-05)

The reboot and route gates passed. The production profile was left restored:
`qwen3.8`, IQ3_S, context 100096, batch 512, ubatch 256, q8 K/V, flash
attention, fit off.

| Requested prompt | Tokenized | Prefill | Decode | Minimum free VRAM | Result |
| ---: | ---: | ---: | ---: | ---: | --- |
| 16,000 | 15,772 | 1,350.7 tok/s | 42.3 tok/s | 697 MiB | pass |
| 32,000 | 31,528 | 1,207.4 tok/s | 39.3 tok/s | 686 MiB | pass |
| 65,536 | 64,574 | 960.0 tok/s | 33.5 tok/s | 695 MiB | pass |
| 98,304 | 96,840 | 801.3 tok/s | 29.9 tok/s | 695 MiB | pass |
| 100,096 | 98,608 | 808.3 tok/s | 26.9 tok/s | 688 MiB | pass |

Every control returned the expected corpus markers, and the stack stayed
healthy after the 100k run. These controls establish a safe 100k operating
point; no challenger has yet been promoted. The next experiment should be a
single-factor Headroom latency ablation (cache/compression policy or memory
tool injection) with a matching Hermes workflow gate, followed by two cold
repetitions before any launcher defaults are changed.

The current route control was refreshed after the ONNX compressor became ready:
an uncached ~6.5k-token request measured 815 prompt tok/s through Headroom and
1,407 prompt tok/s through LiteLLM. A real WSL request using
`172.24.16.1:8787/v1`, `x-headroom-project-id: watson-global`, and
`x-headroom-user-id: juanbeck` returned `WSL_HERMES_ROUTE_OK`. The latency gap
is therefore a measurable proxy overhead, not a reachability failure.

### Rejected Headroom ablation

An isolated no-cache comparison kept the global memory backend but disabled
automatic `memory_save`/`memory_search` tool injection. Two cache-busting arms
showed:

| Arm | Run 1 | Run 2 | Decision |
| --- | ---: | ---: | --- |
| Memory tools enabled | 22.64 s / 310 tok/s, ~7.0k input | 7.96 s / 819 tok/s, ~6.5k input | control |
| Memory tools disabled | 2.88 s / 1,595 tok/s, ~4.6k input | 4.85 s / 845 tok/s, ~4.1k input | reject: loses memory tools |

The challenger was torn down and production port 8787 remained healthy. This
is a performance signal, not a promotion: the next candidate must preserve
memory semantics while reducing schema/context overhead, then pass the Hermes
workflow and persistence gates.

A second semantics-preserving candidate with `--memory-top-k 3` produced the
same ~7.0k/~6.5k input sizes and essentially the same 20.85 s/7.92 s pair as
the control. It was rejected as neutral; the cost is in the tool schema and
memory instruction injection, not the number of recalled durable facts.

The persistence gate itself now has a live CCR proof: a disposable 1760-token
compression returned hash `b703b7700b7a22fa75ead7b0`, and the subsequent
`/v1/retrieve` call returned the original marker-bearing content. The entry is
TTL-bound and is intentionally not part of the durable memory set.
