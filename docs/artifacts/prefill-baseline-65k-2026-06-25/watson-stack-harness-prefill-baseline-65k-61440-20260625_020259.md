# Watson Stack Harness Report — prefill-baseline-65k-61440

Generated: `2026-06-25T00:02:59.994370+00:00`
Base URL: `http://172.24.16.1:4000/v1`
Model: `qwen36-turbo-hermes`
Git: `feat/Foundations_OM_Skills` @ `2289596`

## Endpoint checks

| Check | Status | Elapsed s | OK |
|---|---:|---:|---|
| models | 200 | 0.017 | True |
| headroom | 200 | 0.188 | True |

## Context / throughput

| Target ctx | Status | Elapsed s | Est completion tok/s | Alpha | Omega |
|---:|---:|---:|---:|---|---|
| 61440 | 200 | 15.889 | 2.769 | True | True |

## Tool fidelity

| Test | JSON valid | Tool correct | Elapsed s |
|---|---|---|---:|
| select_read_file | True | True | 2.253 |

## Acceptance

- Result: **PASS** for all executed rows.
- Decode speed median across context rows: `2.769` est tok/s.
