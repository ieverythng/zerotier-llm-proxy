# Historical Watson text baselines

These are bounded summaries of earlier local operator reports, reproduced without workstation routing, credentials, private session records, or images. They are different models/workloads, not a comparative benchmark or a current readiness check. See the [150K Inkling qualification](<watson-inkling-150k-2026-11-25.md>) for the separate native-vision profile.

## Inkling 100K text

Native llama.cpp b12651, 100,096 allocated. A cold text-only run evaluated 98,048 tokens and generated 256. At that length, measured prefill was 305.09 tokens/s, decode 54.38 tokens/s, and time to first token 321.45 s. Minimum observed free VRAM was 2,540 MiB. This was not the native-vision or routed profile.

## Inkling 100K recall

Separate native synthetic conversation: 98,021 actual input, 183 output, 325.39 s wall time, 2,528 MiB minimum observed free VRAM. Three exact markers and the missing/null field passed, with a natural stop. This was not image or tools qualification.

## Saluki 150K text

Historical native Saluki stack, 150,016 allocated, 142,834 actual input, 32 output. Measured prefill 667.80 tokens/s, decode 29.63 tokens/s, time to first token 216.10 s and minimum observed free VRAM 2,618 MiB. Do not infer a speed or quality ranking against the other workload.
