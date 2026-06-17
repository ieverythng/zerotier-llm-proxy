# Lucebox Quick Start Checklist

**Print this. Check boxes as you go.**

---

## ☐ 1. Install Build Tools (Windows)

- [ ] Visual Studio 2022 (Community OK)
  - Workload: "Desktop development with C++"
- [ ] CMake 3.28+ → https://cmake.org/download/
- [ ] Git → https://git-scm.com/

## ☐ 2. Clone + Build Lucebox

```powershell
# PowerShell as Admin or regular user
cd C:\Projects
git clone https://github.com/lucebox-gpu/dflash.git
cd dflash
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release
```

- [ ] Repo cloned
- [ ] CMake configured without errors
- [ ] Build succeeded → `dflash_server.exe` exists
- [ ] Locate the exe: `dir /s dflash_server.exe`

## ☐ 3. Prepare Model File

- [ ] Obtain Lucebox-format model (.lbox or whatever format dflash uses)
- [ ] If only GGUF available, check for conversion tool in `dflash/scripts/`
- [ ] Model file accessible from Windows

## ☐ 4. Start Lucebox Server

```powershell
.\path\to\dflash_server.exe --port 18080 --host 0.0.0.0 --model path/to/model
```

- [ ] Server starts without errors
- [ ] `curl http://localhost:18080/health` returns OK (or equivalent endpoint)
- [ ] Note the exact API endpoint format (OpenAI-compatible? Custom?)

## ☐ 5. Verify ZeroTier Connectivity

From WSL:
```bash
ping 10.88.140.94
curl http://10.88.140.94:18080/health
```

- [ ] Ping succeeds
- [ ] HTTP request reaches Lucebox server
- [ ] Windows firewall allows port 18080 (test from WSL)

## ☐ 6. Configure LiteLLM Proxy

Edit `~/.hermes/litellm/config.yaml`:

```yaml
model_list:
  - model_name: lucebox-qwen
    litellm_proxy_modelType: openai/dynamic_serving
    api_base: "http://10.88.140.94:18080"
    api_key: "not-needed"

litellm_settings:
  pass_through_optional_params: True
  override_auth: True
```

- [ ] Config file updated
- [ ] Restart LiteLLM: `pkill -f litellm && litellm --config ~/.hermes/litellm/config.yaml --port 4000`
- [ ] Proxy starts clean (no errors in first 10 lines of log)

## ☐ 7. Test the Chain

```bash
# Direct to Lucebox via ZeroTier:
curl http://10.88.140.94:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Hello"}]}'

# Via LiteLLM proxy:
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"lucebox-qwen","messages":[{"role":"user","content":"Hello"}]}'
```

- [ ] Direct test returns a valid response
- [ ] Proxy test returns a valid response
- [ ] Response contains actual generated text (not just empty)

## ☐ 8. Update Hermes Routing

Edit `~/.hermes/config.yaml` — add or update model provider:

```yaml
model_providers:
  custom:
    - name: zerotier-lucebox
      provider: openai
      api_base: "http://127.0.0.1:4000"
      api_key: "not-needed"
      models:
        - lucebox-qwen
```

- [ ] Hermes config updated
- [ ] `hermes models --list` shows lucebox-qwen
- [ ] Test chat: `echo "test" | hermes chat --model lucebox-qwen`

## ☐ 9. Benchmark

```bash
# Time a simple completion
time curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"lucebox-qwen","messages":[{"role":"user","content":"Write a haiku"}]}'
```

- [ ] Record tokens/sec
- [ ] Compare vs current llama.cpp baseline
- [ ] Note any quality differences in output

---

## Common Errors Quick Fix

| Error | Fix |
|-------|-----|
| CMake can't find compiler | Install VS 2022 C++ workload |
| Build errors about CUDA/DX | Lucebox uses DirectCompute, not CUDA — check docs |
| Port 18080 refused from WSL | Windows firewall → allow inbound TCP 18080 |
| ZeroTier ping fails | `zerotier-cli leave <net>` then `zerotier-cli join <net>` on both ends |
| LiteLLM 404 on model | Check `model_name` in config matches what you request in API call |
| "model not found" from Lucebox | Check model path, format, and that dflash supports it |

---

*Quick reference — see lucebox-implementation-guide.md for full details.*
