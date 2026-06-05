# Server Agent Handoff

This repo intentionally supports a Windows server role, but the Linux agent should not implement or run that side.

The Windows/server agent owns:

- Installing `litellm[proxy]`.
- Maintaining `config/server/litellm-config.yaml`.
- Starting `llama.cpp` on `127.0.0.1:8080`.
- Starting LiteLLM on `0.0.0.0:4000`.
- Adding Windows Firewall rules for TCP 4000 when needed.
- Tuning Hermes launch values through `scripts/windows/Start-Qwen36ZeroTierStack.ps1`.
- Verifying local Windows endpoints:
  - `http://127.0.0.1:8080/v1/models`
  - `http://127.0.0.1:4000/v1/models`
- Verifying context mode consistency with `scripts/windows/Test-Qwen36ContextMode.ps1`.

Client-facing contract:

- The server should expose `http://10.88.140.94:4000/v1`.
- The model id should be `qwen36-turbo-hermes`.
- Clients should register provider `qwen36-zerotier` in `~/.codex/config.toml`; it should not be assumed as the default model.
- The proxy should support:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
- If Linux routes `10.88.140.94` over Wi-Fi instead of ZeroTier, fix the client with `sudo zerotier-cli set 3b19b3a716937e29 allowManaged=1` and reconnect ZeroTier.

Current runtime mapping:

- `zerotier-llm-proxy` starts the active Hermes launcher at `C:\Users\Admin\PROJECTS\llama-cpp-server\scripts\start_turbo_hermes.ps1`.
- That launcher stages binaries from `C:\Users\Admin\PROJECTS\llama-cpp-turboquant\build-cuda-faall\bin`, which is the turboquant llama.cpp build currently used by the stack.
- The default Hermes profile is `hermes-qwen36-64k` with `-ContextSize 65536`, `q8_0` K cache, `turbo2` V cache, Flash Attention on, and `-np 1`.
- Use `scripts/windows/Measure-Qwen36ProxyThroughput.ps1` after each context or KV-cache change to compare completion tok/s through the same LiteLLM `/v1/responses` path used by Codex.

Measured operating points on this host:

- `65536`: practical default. After restore, llama.cpp reported `n_ctx=65536`; `nvidia-smi` showed about `14856 MiB / 16303 MiB` used.
- `98304`: verified start. A `98304` synthetic context benchmark through LiteLLM completed in about `152.53s` for a short answer.
- `131072`: verified start. llama.cpp reported `n_ctx=131072`; `nvidia-smi` showed about `15840 MiB / 16303 MiB` used and sustained `100%` GPU during the interrupted full-context request.
- Use `scripts/windows/Invoke-QwenContextSweep.ps1` for repeatable context sweeps. It stops/restarts llama.cpp per context size, records VRAM snapshots, runs the LiteLLM benchmark, writes `_tmp/bench/context-sweep-*/summary.csv`, and restores `65536` by default.
- Use `scripts/windows/Invoke-QwenKvCacheSweep.ps1` for fixed-context KV-cache and batch sweeps. It drives the existing local `llama-cpp-server` launch scripts without editing either llama.cpp checkout, writes `_tmp/bench/kv-sweep-*/summary.csv`, and restores the normal Hermes launcher by default.
- Use `scripts/windows/Compare-QwenSweepResults.ps1` to normalize and rank all `_tmp/bench/**/summary.csv` rows by prompt size, completion tok/s, and VRAM headroom.
- 65k KV result on 2026-06-05: short-decode prompt favored `q8_0/turbo2` over `q8_0/turbo3` (`8.729` vs `5.191` completion tok/s). An 8k synthetic prompt kept them effectively tied, with `q8_0/turbo2` slightly ahead (`3.983` vs `3.961` completion tok/s) and leaving more VRAM free (`1147 MiB` vs `1070 MiB`). Keep `q8_0/turbo2` as the default.

Recommended workflow:

- Run normal Hermes/Discord traffic at `65536`.
- Restart with `-ContextSize 98304` only when the session needs a one-off large-context recovery or audit.
- Use `-ContextSize 131072` only for stress testing; it leaves roughly `100-200 MiB` VRAM free and can stall the shared endpoint.
- Preserve cross-compaction knowledge in a small session ledger outside the model request. Keep the ledger to stable facts, current goals, known constraints, unresolved decisions, and file paths. Inject the ledger summary after compaction instead of depending on larger server context alone.
- Use `docs/session-ledger.md` and `scripts/windows/Update-QwenSessionLedger.ps1` for a repo-local ledger workflow. The default ledger path is `_tmp/session-ledger.md`, which stays untracked.
- Before handing a large-context mode to Codex, run `scripts/windows/Test-Qwen36ContextMode.ps1 -ExpectedContextWindow <ctx>` so server context and installed profile context do not silently diverge.

