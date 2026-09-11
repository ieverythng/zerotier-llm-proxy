# Watson Qwen3.8 endpoint repair

Date: 2026-09-03

## Result

The endpoint is reachable and coherent through direct llama.cpp, LiteLLM,
Headroom, and the ZeroTier address `10.88.140.94:4000`. Hermes is active and
Discord is connected.

`Start-WatsonStack.ps1` now selects the official CUDA 13.3 build
`llama-b10621-win-cuda133` for Qwen3.8, verifies the executable path, and runs
two exact-marker generation probes before continuing. A Qwen3.8 request is
served at `n_ctx=100096` with the existing Unsloth non-thinking defaults.

## Root cause of the stall

The previous default selected the TurboQuant `9418 (2cbfdc62)` binary. With the
IQ3_S GGUF it produced malformed chat-parser output and long, nonsensical
generation. The separate local `d08c787` build bounded output but was also
incoherent. The official CUDA 13.3 `10621 (c1d0e7a00)` binary returns the exact
marker twice, honors the token cap, streams correctly, and emits valid tool and
strict-JSON responses.

The Discord turn recorded at 17:58 was a different failure mode: the message
contained text referring to earlier pictures, but no usable image input was
available to the text-only Qwen3.8 model. Hermes vision credentials were also
unavailable, so it selected deferred web-search tools and spent 251 seconds on
26 calls before delivering a short failure response. This is expected tool
routing behavior for an unavailable vision backend, not endpoint corruption.

## Headroom/Hermes changes

- Headroom memory remains enabled with global storage.
- Sticky memory-tool replay is disabled via the supported
  `HEADROOM_TOOL_INJECTION_STICKY=disabled` rollback because v0.37 can replay
  Chat-Completions function schemas into the Responses API. Per-request
  injection preserves the correct schema for both APIs.
- Hermes background review is disabled to keep the single model slot available
  for Discord/NAO work; persistent memory is unaffected.
- Compression remains at the requested `0.80` threshold (about 33% remaining at
  the current 100,096-token allocation) and uses direct LiteLLM chat completions
  so a summary cannot acquire memory tools.
- The Hermes sync resolves the WSL host gateway dynamically instead of relying
  on a stale `172.24.16.1` address.

## Evidence

- [Endpoint contract probe](../artifacts/qwen38-endpoint-repair-2026-09-03/headroom-chat-contract-final.json)
- [ZeroTier/Linux contract probe](../artifacts/qwen38-endpoint-repair-2026-09-03/wsl-zerotier-contract.json)
- [Headroom workflow gate](../artifacts/qwen38-endpoint-repair-2026-09-03/headroom-workflow-final.json)
- [Launcher regression checks](../../tests/test_watson_launcher.ps1)

The remaining limitation is vision: Qwen3.8 IQ3_S can serve text, tool calls,
and JSON, but cannot inspect Discord images without a separate multimodal model
and projector or a working external vision provider.
