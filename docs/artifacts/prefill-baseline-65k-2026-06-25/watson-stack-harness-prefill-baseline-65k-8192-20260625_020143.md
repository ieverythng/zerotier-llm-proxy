# Watson Stack Harness Report — prefill-baseline-65k-8192

Generated: `2026-06-25T00:01:43.161738+00:00`
Base URL: `http://172.24.16.1:4000/v1`
Model: `qwen36-turbo-hermes`
Git: `feat/Foundations_OM_Skills` @ `2289596`

## Endpoint checks

| Check | Status | Elapsed s | OK |
|---|---:|---:|---|
| models | 200 | 0.017 | True |
| headroom | 200 | 0.003 | True |

## Context / throughput

| Target ctx | Status | Elapsed s | Est completion tok/s | Alpha | Omega |
|---:|---:|---:|---:|---|---|
| 8192 | 200 | 4.606 | 6.513 | True | True |

## Tool fidelity

| Test | JSON valid | Tool correct | Elapsed s |
|---|---|---|---:|
| select_read_file | True | True | 1.603 |

## Acceptance

- Result: **PASS** for all executed rows.
- Decode speed median across context rows: `6.513` est tok/s.
