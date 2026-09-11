# Qwen3.8 evaluation artifacts

This directory contains the dated autoresearch evidence for the Qwen3.8
challengers, the Watson control, and the final routed stack.

## Key files

- `qwen36-control-*.csv/json`: Watson Qwen3.6 Q3_K_M control measurements.
- `qwen38-q3kxl-*.csv/json/log`: Qwen3.8 Q3_K_XL short/high-context challenger,
  including the stopped 100k prefill-cliff trace.
- `qwen38-iq3s-official-cuda133-*.csv/json/log`: selected IQ3_S benchmark,
  qualitative, context-sweep, and server evidence.
- `qwen38-iq3s-100k-headroom-workflow-post-update.json`: four-case workflow
  gate through Headroom after package updates; all four cases pass.
- `qwen38-iq3s-100k-headroom-global-workflow-final.json`: final four-case gate
  after enabling explicit global memory storage; all four cases pass.
- `headroom-health-post-update.json`: Headroom 0.37.0 health snapshot.
- `qwen38-iq3s-headroom-memory-roundtrip.json`: disposable save/search proof;
  the test fact is deleted before completion.
- `qwen38-iq3s-local-compression-probe.json`: IQ3_S-generated compact JSON
  summary through direct LiteLLM. This demonstrates a manual fallback, not
  automatic Codex compaction integration.
- `*-pip-freeze-{before,after}.txt` and `litellm-{before,after}.txt`: package
  upgrade evidence.

The selected live alias is `qwen36-turbo-hermes` for compatibility with the
existing Hermes/LiteLLM configuration; the loaded GGUF is Qwen3.8 IQ3_S.
