# Experiment ranking by `prompt_per_second` (max)

| Rank | Step | Decision | Variant | Metric | Target | Elapsed s | Prompt n | Source |
|---:|---:|---|---|---:|---:|---:|---:|---|
| 1 | 3 | keep | b1024-ub1024 | 1340.942 | 61440 | 32.648 | 42092 | `_tmp/bench/direct-prefill-batch-sweep-65k/single-b1024-ub1024-20260625_033009.csv` |
| 2 | 10 | no-promote | challenger-b1024-ub1024-repeat2-42k | 1326.823 | 42000 | 31.489 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-repeat2-42k-20260626_203547.csv` |
| 3 | 9 | needs-repeat | challenger-b1024-ub1024-42k | 1319.205 | 42000 | 31.683 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub1024-42k-20260626_202237.csv` |
| 4 | 4 | repeat | b1024-ub1024-repeat | 1307.122 | 61440 | 33.100 | 42080 | `_tmp/bench/autoresearch-prefill-loop/repeat-b1024-ub1024-61k-20260625_034455.csv` |
| 5 | 23 | instrumented-repeat-pass | challenger-b1024-ub512-stream-repeat2-42k | 1304.722 | 42000 | 32.660 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-stream-repeat2-42k-20260711_212116.csv` |
| 6 | 11 | needs-task-gate | challenger-b1024-ub768-42k | 1300.004 | 42000 | 32.136 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-42k-20260626_203954.csv` |
| 7 | 1 | keep | b1024-ub512 | 1294.740 | 61440 | 33.766 | 42091 | `_tmp/bench/direct-prefill-batch-sweep-65k/single-b1024-ub512-20260625_031650.csv` |
| 8 | 22 | instrumented-challenger | challenger-b1024-ub512-stream-42k | 1290.782 | 42000 | 33.049 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-stream-42k-20260711_212031.csv` |
| 9 | 2 | discard | b2048-ub512 | 1289.601 | 61440 | 33.869 | 42091 | `_tmp/bench/direct-prefill-batch-sweep-65k/single-b2048-ub512-20260625_032258.csv` |
| 10 | 14 | needs-repeat-task-gate | challenger-b1024-ub512-42k | 1288.665 | 42000 | 32.412 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-42k-20260626_210138.csv` |
| 11 | 15 | synthetic-repeat-pass | challenger-b1024-ub512-repeat2-42k | 1287.496 | 42000 | 32.445 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub512-repeat2-42k-20260626_211055.csv` |
| 12 | 21 | instrumented-baseline | baseline-default-stream-42k | 1246.247 | 42000 | 35.775 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-stream-42k-20260711_211511.csv` |
| 13 | 8 | baseline | baseline-default-42k | 1235.880 | 42000 | 33.821 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-42k-20260626_201802.csv` |
| 14 | 13 | baseline | baseline-default-repeat2-42k | 1224.956 | 42000 | 34.083 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-repeat2-42k-20260626_205655.csv` |
| 15 | 25 | instrumented-baseline-repeat | baseline-default-stream-repeat2-42k | 1224.286 | 42000 | 34.792 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/baseline-default-stream-repeat2-42k-20260711_212654.csv` |
| 16 | 0 | baseline | current-65k-default | 1213.997 | 61440 | 39.873 | 43917 | `_tmp/bench/direct-prefill-current-65k/direct-prefill-current-65k-20260625_030822.csv` |
| 17 | 6 | measure | fixed-current-live-42k | 1193.416 | 42000 | 41.916 | 41382 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/fixed-current-42k-20260626_194012.csv` |
| 18 | 12 | discard | challenger-b1024-ub768-repeat2-42k | 1162.069 | 42000 | 36.081 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-b1024-ub768-repeat2-42k-20260626_205018.csv` |
| 19 | 30 | mtp-prefill-regression | mtp-stream-42k | 1006.671 | 42000 | 43.448 | 41383 | `docs/artifacts/autoresearch-prefill-loop-2026-06-25/challenger-mtp-stream-42k-20260711_2140.csv` |
| 20 | 5 | discard | b1024-ub1024-repeat3 | 987.449 | 61440 | 43.522 | 41012 | `_tmp/bench/autoresearch-prefill-loop/repeat3-b1024-ub1024-61k-20260625_040209.csv` |
