# webchat2api Integration Guide

webchat2api wraps ChatGPT Web (and Grok/Gemini) into a standard OpenAI-compatible API. Use it as an "Oracle" layer — routing expert reasoning tasks to GPT-5/5.5 models through your ChatGPT Plus subscription.

## Architecture

```
[Hermes/Watson] → curl http://localhost:9000/v1/chat/completions → [webchat2api] → [ChatGPT Web API] → [OpenAI GPT-5.x]
```

- Runs on **port 9000** (localhost)
- Auth via `x-api-key` header (default: `admin`)
- Models available: `gpt-5`, `gpt-5-5`, `gpt-5-5-thinking`, plus all ChatGPT Web models

## Quick Start (WSL Linux)

### 1. Clone & Install

```bash
git clone https://github.com/zqbxdev/webchat2api.git ~/webchat2api
cd ~/webchat2api/src
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Copy `config.example.json` to `config.json`. Key settings:

```json
{
  "auth-key": "admin",
  "refresh_account_interval_minute": 60,
  "auto_remove_invalid_accounts": true,
  "enable_turnstile_solver": true
}
```

### 3. Add Your ChatGPT Account

Create `data/accounts.json`:

```json
[
  {
    "access_token": "<YOUR_ACCESS_TOKEN>",
    "type": "plus",
    "provider": "gpt",
    "status": "正常",
    "email": "your@email.com"
  }
]
```

**Getting your access token:** See `scripts/extract-token.md` for the browser console method.

### 4. Start the Server

```bash
cd ~/webchat2api/src
PORT=9000 .venv/bin/python main.py
```

Verify: `curl http://localhost:9000/v1/models`

## Windows Setup

Use `start-webchat2api.bat` to launch from PowerShell or double-click. Requires Python 3.11+ installed.

## Docker Compose

See `docker-compose.yml` at repo root — runs webchat2api alongside LiteLLM proxy.

## Usage with Hermes Oracle Script

```bash
bash /path/to/Watson/scripts/ask-gpt5.sh "Your expert question" gpt-5-5-thinking
```

The script handles thinking budget, streaming, and parsing automatically.

## Auth Troubleshooting

### "密钥无效或已失效，请重新登录" (Invalid or Expired Key)

OpenAI periodically rotates access tokens. When this happens:

1. Open chat.openai.com in your browser
2. Open DevTools (F12) → Application → Cookies
3. Copy the `__Secure-access_token` value
4. Update `data/accounts.json` with the new token
5. Restart webchat2api

### "Turnstile Challenge"

webchat2api has a built-in Turnstile solver (`enable_turnstile_solver: true`). If it still fails, update your browser fingerprint in config.json's `chatgpt_fingerprint` section.

## Rate Limits

ChatGPT Plus limits (~50 messages per 8-hour window for GPT-5 models). Monitor usage via the web admin panel at `http://localhost:9000/accounts`.

## Security Notes

- Change the default `auth-key` from "admin" for any network-exposed deployment
- Access tokens are sensitive — don't commit `data/accounts.json` to version control
- webchat2api is for personal/educational use only per its license
