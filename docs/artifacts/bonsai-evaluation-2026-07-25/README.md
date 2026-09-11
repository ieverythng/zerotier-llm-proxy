# Watson vs Ternary Bonsai evaluation artifacts

This directory contains the reproducible measurements used by
`docs/reports/watson-bonsai-evaluation-2026-07-29.md`.

## Compared models

- Control: `D:\MODELS\Qwen3.6-27B-Q3_K_M.gguf`
- Challenger: `D:\MODELS\Ternary-Bonsai-27B-Q2_0.gguf`
- Control runtime: the existing TurboQuant llama.cpp build
- Challenger runtime: PrismML's `prism` llama.cpp fork at
  `7529fdaaf99ffdc5ca71ace9c7409a56b27ad92f`

The stock TurboQuant build was also tried first. It rejected Bonsai's group-128
`Q2_0` tensors in `ggml-cpu/ops.cpp`, so the Prism fork was required.

## Artifact groups

- `watson-control-*`: fresh 65,536-context control measurements.
- `bonsai-author-default-*`: author-compatible auto-fit measurements.
- `bonsai-c102400-*`: explicit 102,400-context measurements.
- `bonsai-kv4-*`: Q4_0 K/V cache measurements at 102,400 and 131,072
  allocated context.
- `*-decode-512-*`: forced 512-token decode repeats.
- `*-workflow*`: four-case Hermes compatibility and retention gates.
- `*-qualitative*`: exact response, code/JSON, constraint, uncertainty, and
  Discord-style evaluations.
- `watson-restored-workflow.*`: final production acceptance run after restoring
  Watson.
- `watson-restored-after-kv4-workflow.*`: acceptance run after the follow-on
  KV4 cycle.
- `results.csv`: compact decision ledger.

Thinking was disabled for the fair visible-answer qualitative and final routed
Bonsai gates. Through LiteLLM this requires
`extra_body.chat_template_kwargs.enable_thinking=false`; the provider-specific
field is dropped when sent at the top level.

The KV4 experiments used `-fa on -ctk q4_0 -ctv q4_0` without a model-specific
mean-centering bias. Prism documents that this uncalibrated mode can lose some K
cache accuracy, so these runs establish capacity and retention rather than
quality equivalence.
