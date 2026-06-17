# Lucebox Implementation Guide

**Date:** 2026-06-15
**Status:** Ready for implementation
**Target:** Lucebox dflash_server via ZeroTier LiteLLM proxy

---

## Architecture Overview

```
[WSL Linux]                    [Windows Host]
─────────────                  ────────────────
LiteLLM Proxy
port 4000                      Lucebox dflash_server
                               port 18080
        │                              │
        └── ZeroTier (10.88.140.x) ───┘
```

**The flow:** Hermes → LiteLLM proxy (WSL :4000) → ZeroTier → Lucebox server (Windows :18080)

---

## Phase 1: Build Lucebox from Source

### Prerequisites (Windows Host)

Install on Windows PC:
- **Visual Studio 2022** (Community edition is fine) — required for MSVC compiler + CMake integration
- **CMake** 3.28+ — https://cmake.org/download/
- **Git** — https://git-scm.com/

### Clone and Build

```powershell
# Clone the repo
cd C:\Projects  # or wherever you keep projects
git clone https://github.com/lucebox-gpu/dflash.git
cd dflash

# Build with CMake
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release
```

The build produces `dflash_server.exe` in `build\src\Release\` or similar.

### Key Files

- `src/server/` — server implementation
- `CMakeLists.txt` — build configuration
- `requirements.txt` — Python dependencies (if any hybrid components)

---

## Phase 2: Run Lucebox Server

### Basic Startup

```powershell
# From the build directory
.\src\server\dflash_server.exe --help
```

Expected flags (based on typical inference servers):
- `--port` — listening port (default likely 8080)
- `--model` — path to model file
- `--host` — bind address (use `0.0.0.0` for ZeroTier access)

### Model Format

Lucebox uses its own format (dlpack/dtensor). You'll need to:
1. Convert GGUF → Lucebox format, OR
2. Use a pre-converted Lucebox model if available

Check `dflash` repo for conversion scripts or `dflash_convert` tool.

### Startup Command

```powershell
.\src\server\dflash_server.exe --port 18080 --host 0.0.0.0 --model path/to/model.lbox
```

**Verify it's running:**
```powershell
curl http://localhost:18080/health
# or whatever the health endpoint is
```

---

## Phase 3: LiteLLM Proxy Configuration

### Update config.yaml

On WSL, edit `~/.hermes/litellm/config.yaml`:

```yaml
model_list:
  - model_name: lucebox-qwen
    litellm_proxy_modelType: openai/dynamic_serving
    litellm_proxy_budget_policy:
      default_budget: "$1000"
    api_base: "http://10.88.140.94:18080"
    api_key: "not-needed"

litellm_settings:
  pass_through_optional_params: True
  override_auth: True
```

### Restart LiteLLM

```bash
# Find and kill existing proxy
pkill -f litellm
# Or if running as systemd service:
systemctl restart litellm-proxy

# Or if running manually:
litellm --config ~/.hermes/litellm/config.yaml --port 4000
```

### Update Hermes Model Routing

In `~/.hermes/config.yaml`, update model routing to point to the LiteLLM proxy:

```yaml
model_providers:
  custom:
    - name: zerotier-proxy
      provider: openai
      api_base: "http://127.0.0.1:4000"
      api_key: "not-needed"
      models:
        - lucebox-qwen
```

---

## Phase 4: Testing

### Step 1: Lucebox Direct Test

From WSL, test Lucebox directly over ZeroTier:

```bash
curl http://10.88.140.94:18080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Hello"}]}'
```

### Step 2: LiteLLM Proxy Test

```bash
curl http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"lucebox-qwen","messages":[{"role":"user","content":"Hello"}]}'
```

### Step 3: Hermes Integration Test

```bash
hermes models --list
# Verify lucebox-qwen appears

# Test via Hermes:
echo "Test message" | hermes chat --model lucebox-qwen
```

---

## Troubleshooting

### Build Fails
- Ensure Visual Studio 2022 is installed with "Desktop development with C++" workload
- Check CMake version: `cmake --version` (need 3.28+)
- Try `cmake .. -DCMAKE_BUILD_TYPE=Debug` for more verbose errors

### Lucebox Won't Start
- Check port conflicts: `netstat -ano | findstr :18080`
- Verify model file exists and is in correct format
- Run with `--verbose` flag for debug output

### ZeroTier Connectivity
- From WSL: `ping 10.88.140.94`
- Check ZeroTier network status: `zerotier-cli listnetworks`
- Ensure Windows firewall allows port 18080

### LiteLLM Proxy Issues
- Check logs: `journalctl -u litellm-proxy -f` (if systemd)
- Verify config syntax: `litellm --config ~/.hermes/litellm/config.yaml --debug`
- Ensure `pass_through_optional_params: True` is set

---

## Model Conversion (If Needed)

If Lucebox requires its own model format:

```bash
# Check if dflash includes a conversion tool
ls dflash/scripts/
# Look for convert.py or similar

# Typical conversion:
python dflash/scripts/convert_gguf_to_lbox.py \
  --input /path/to/model.gguf \
  --output /path/to/model.lbox
```

If no conversion tool exists, you may need to:
1. Check Lucebox Discord/GitHub for pre-converted models
2. Use the `dflash` Python package if available: `pip install dflash`
3. Contact Lucebox maintainers for model format specs

---

## Performance Expectations

Based on Lucebox claims:
- **20-100x faster** than traditional inference engines
- GPU-accelerated via DirectCompute (Windows)
- Optimized for RTX 5070 Ti architecture

Expected throughput improvements over llama.cpp:
- Token generation speed: 3-5x faster
- Memory efficiency: significantly lower VRAM usage
- Latency: reduced first-token latency

---

## Rollback Plan

If Lucebox doesn't work out:

1. Revert LiteLLM config to point back to llama.cpp:
   ```yaml
   api_base: "http://172.24.16.1:8080"
   ```

2. Restart LiteLLM proxy

3. Hermes routing stays the same — only the upstream target changes

---

## Next Steps After Implementation

1. **Benchmark:** Compare token/sec vs current llama.cpp setup
2. **Stability test:** Run 100+ requests to check for crashes
3. **Model support:** Test with Qwen 3.6 27B and other models
4. **Production config:** Tune batch size, context length, etc.

---

*Generated: 2026-06-15 | Author: WatsonOW | Review: pending*