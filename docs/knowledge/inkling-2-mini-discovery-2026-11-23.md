# Inkling 2 Mini Mix-12G on the Watson workstation

## Artifact and runtime

- `Inkling-2-Mini-abliterated-Mix-12G.gguf` (11,634,058,560 bytes; 10.83 GiB) matches [the published file length](https://huggingface.co/api/models/orcarouter/Inkling-2-Mini-abliterated-GGUF/tree/main), and the local file is GGUF v3. A local SHA-256 was computed, but the downloaded artifact was not independently authenticated against a publisher digest.
- In `inkling2_loop`, the same 12 core layers run three times inside a 24-layer physical network. Weights are shared, but loop passes maintain separate state. Reasoning effort changes the prompt, not the loop count. See the [upstream config](https://huggingface.co/thinkingmachines/Inkling-2-Mini/blob/main/config.json), [model card](https://thinkingmachines.ai/model-card/inkling-2-mini/), and [pinned implementation](https://github.com/ggml-org/llama.cpp/blob/b12633/src/models/inkling2-loop.cpp).
- Orca's [quant card](https://huggingface.co/orcarouter/Inkling-2-Mini-abliterated-GGUF#pick-a-build) reports 16.5 GiB peak at 32K on H100 and tests the exact GGUF on stock [llama.cpp b12633](https://github.com/ggml-org/llama.cpp/releases/tag/b12633). This is **not** a 12 GB VRAM guarantee. The GGUF is text-only; the nominal 1M context is not a tested workstation capacity.
- Use the embedded Jinja template, low reasoning effort and bounded output. Mini support landed in [llama.cpp #32694](https://github.com/ggml-org/llama.cpp/pull/32694). CPU/host-op offload can corrupt output on some systems ([#32710](https://github.com/ggml-org/llama.cpp/issues/32710), [#32778](https://github.com/ggml-org/llama.cpp/issues/32778)); `--no-op-offload` is a reported mitigation, not a general guarantee.

## Local observations (RTX 5070 Ti, 16 GiB RAM)

| Profile | Guarded local result |
| --- | --- |
| Saluki b11433, 150,016 / Q4_0 KV, F16 projector loaded | Authenticated model/context metadata; two text canaries passed. After canaries: 7.22 GiB available RAM, 2,895 MiB free VRAM. See [earlier longer-prompt, image and tool tests](../reports/watson-bonsai-saluki-evaluation-2026-10-09.md#L40-L102). |
| Inkling b12633, 8,192 / F16 KV, auto-fit, default model I/O | Aborted during load after available host memory fell below the 2 GiB floor. |
| Inkling b12633, 8,192 / F16 KV, direct I/O, batch 128, microbatch 64, fit margin 2,450 MiB, `--no-op-offload` | Exact context and both tiny text canaries passed. Sampled 6.40 GiB RAM / 3,367 MiB VRAM free after inference. Observed decode about 67–71 tok/s in these short tests only. |

These are live, isolated direct-backend checks with short prompts. An allocated 8,192 context does not mean 8,192 useful prompt tokens plus output. The launcher does not advertise a larger Inkling profile based on publisher specs alone.

## Gateway and staged proxy checks

On the same workstation, the isolated gateway → Headroom 0.45.0 → LiteLLM 1.110.2 → llama.cpp chain returned `42` from both models and completed an Inkling SSE response including `[DONE]`. It returned HTTP 409 on a switch while stream teardown still held admission. Once idle, it switched Inkling → Saluki → Inkling, confirmed exact model/context after each load, and returned `LOCAL_OK` from each model. Both emitted the expected arguments for one synthetic tool-call request; no tool was executed. Saluki recognized the color of an inline synthetic image through the same chain. Inkling also retrieved a planted verification code from a 2,665-prompt-token document (71 generated tokens, about 4.8 s wall time). The post-request sample had 5.30 GiB available RAM and 3,425 MiB free VRAM. These do not qualify the full 8,192-token window or complex tool chains.

## DSH 0.2.2-rc.5 smoke checks (2026-11-24)

The saved Desktop provider has both model IDs and the same numeric context/output limits as the gateway. These inference tests used the installed DSH **headless** runner with a temporary home and local-only provider overlay, not a submitted turn in the Desktop GUI. The Desktop default was not changed.

- **Saluki:** standard headless tool inventory, read-only permission, one short test message. The run completed with the exact expected response and no tool calls at the 150,016-token allocation.
- **Inkling:** the same headless tool inventory triggered the switch to Inkling, but llama.cpp rejected the 9,524-token request at 8,192 allocated tokens. The short user message alone was not the limiting factor. Do not treat Inkling's 8K profile as qualified for an ordinary tool-heavy DSH agent turn.
- **Inkling, lean test:** a minimal system prompt and disabled tool plugins in the temporary headless overlay completed the same test with the exact expected response and no tool calls. Inkling remained loaded at 8,192 with about 6.03 GiB available RAM and 3,543 MiB free VRAM in the post-test sample. No lean Desktop preset was installed, and no larger Inkling context was validated.

The gateway accepts the bundled DSH adapter's `store: false` without passing a storage directive to the downstream proxies; it still rejects storage requests. The Windows launcher ACL fix applies inheritable permissions to the private directory without recursively stripping permissions from the lock and log files.
