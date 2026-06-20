# WSL Scripts

This directory contains scripts that run inside WSL (Ubuntu).

## Headroom Proxy

**Headroom runs on Windows, NOT in WSL.** Do not install or start a headroom proxy inside WSL.

Hermes (running in WSL) reaches the Windows headroom proxy via the host bridge:
- Windows headroom listens on `0.0.0.0:8787`
- Hermes connects to `http://172.24.16.1:8787/v1`

To start headroom, use the Windows scripts from PowerShell:
```powershell
.\scripts\windows\Start-HeadroomHermes.ps1
# or full stack:
.\scripts\windows\Start-Qwen36ZeroTierStack.ps1 -EnableHeadroom -RouteHermesThroughHeadroom
```

## Scripts in this directory

| Script | Purpose |
|--------|---------|
| `install-codex-client-config.sh` | Configure Codex CLI to use ZeroTier LiteLLM endpoint |
| `verify-client.sh` | Verify client connectivity to the stack |
