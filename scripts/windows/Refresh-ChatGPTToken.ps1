# Refresh-ChatGPTToken.ps1
# =============================================
# Extracts a fresh __Secure-access_token from your browser's cookie store
# and updates webchat2api's accounts.json for GPT-5 Oracle access.
#
# Supports: Google Chrome, Microsoft Edge
#
# Usage:
#   .\Refresh-ChatGPTToken.ps1
#   .\Refresh-ChatGPTToken.ps1 -Browser Edge
#   .\Refresh-ChatGPTToken.ps1 -Webchat2ApiPath "C:\path\to\webchat2api"
# =============================================

param(
    [ValidateSet("Chrome", "Edge")]
    [string]$Browser = "Chrome",
    [string]$Webchat2ApiPath = "/home/juanbeck/webchat2api",
    [switch]$RestartProxy,
    [int]$Webchat2ApiPort = 9000
)

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg); Write-Host ("`n[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg); Write-Host ("  ✓ {0}" -f $Msg) -ForegroundColor Green }
function Write-Warn { param([string]$Msg); Write-Host ("  ⚠ {0}" -f $Msg) -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg); Write-Host ("  ✗ {0}" -f $Msg) -ForegroundColor Red }

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "║     ChatGPT Token Refresh — webchat2api Oracle      ║" -ForegroundColor Magenta
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Magenta

# ─── Step 1: Verify you're logged in ──────────────────────────────
Write-Step "Step 1: Checking browser login status"

$chromePath = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data\Default"
$edgePath   = Join-Path $env:LOCALAPPDATA "Microsoft\Edge\User Data\Default"

if ($Browser -eq "Chrome") {
    $userDataDir = $chromePath
    $cookieDb    = Join-Path $userDataDir "Network\cookies"
} else {
    $userDataDir = $edgePath
    $cookieDb    = Join-Path $userDataDir "Network\cookies"
}

if (-not (Test-Path $cookieDb)) {
    Write-Fail "Cookie database not found at: $cookieDb"
    Write-Host "  Try the other browser: .\Refresh-ChatGPTToken.ps1 -Browser Edge" -ForegroundColor Yellow
    exit 1
}

# ─── Step 2: Extract token from cookie store ──────────────────────
Write-Step "Step 2: Extracting access token from $Browser cookies"

try {
    # Use Python to read the SQLite cookie database — much more reliable than
    # trying to do AES decryption in PowerShell
    $pythonScript = @"
import sqlite3
import sys
import os

cookie_db = r"$cookieDb"

# Try to connect — if Chrome is running, the DB is locked
try:
    conn = sqlite3.connect(cookie_db)
    cursor = conn.cursor()
    
    # Query for chat.openai.com access token
    # The cookie name varies by browser version
    queries = [
        "SELECT encrypted_value FROM cookies WHERE name = '__Secure-access_token' AND host_key = '.chat.openai.com'",
        "SELECT encrypted_value FROM cookies WHERE name = 'access_token' AND host_key LIKE '%openai%'",
        "SELECT value FROM cookies WHERE name = '__Secure-access_token' AND host_key = '.chat.openai.com'",
    ]
    
    token = None
    for query in queries:
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
            if rows:
                raw = rows[0][0]
                # If it's already plaintext (unencrypted), use it directly
                if isinstance(raw, str) and raw.startswith('eyJ'):
                    token = raw
                    break
                # Otherwise it's encrypted — we need DPAPI/cryptoapi
                # For now, fall through to the next query
        except:
            continue
    
    conn.close()
    
    if token:
        print(f"TOKEN:{token}")
    else:
        # Token is encrypted with Chrome's master key — need OS crypto
        print("ENCRYPTED")
        
except sqlite3.OperationalError as e:
    if "database is locked" in str(e):
        print("LOCKED")
    else:
        print(f"ERROR:{e}")
except Exception as e:
    print(f"ERROR:{e}")
"@

    # Check if Python is available
    $pythonExe = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonExe) {
        $pythonExe = Get-Command python3 -ErrorAction SilentlyContinue
    }
    
    if (-not $pythonExe) {
        Write-Fail "Python not found. Install Python or extract the token manually."
        exit 1
    }

    $result = & python -c $pythonScript 2>&1
    Write-Host "  Python output: $result" -ForegroundColor DarkGray

} catch {
    Write-Warn "SQLite extraction failed: $($_.Exception.Message)"
    $result = "ERROR"
}

# ─── Handle extraction results ──────────────────────────────────────
if ($result -match "^TOKEN:(.+)$") {
    $accessToken = $matches[1]
    Write-Ok "Access token extracted successfully (length: $($accessToken.Length) chars)"

} elseif ($result -eq "LOCKED") {
    Write-Warn "Chrome/Edge cookie database is locked — browser is running."
    Write-Host ""
    Write-Host "  Please close ALL Chrome/Edge windows, then retry." -ForegroundColor Yellow
    Write-Host "  Or use the manual method below." -ForegroundColor Yellow
    
    # Fall through to manual extraction

} elseif ($result -eq "ENCRYPTED") {
    Write-Warn "Token is encrypted (Chrome DPAPI encryption)."
    Write-Host ""
    Write-Host "  Chrome encrypts cookies with DPAPI. Extracting requires" -ForegroundColor Yellow
    Write-Host "  the Windows master key + CryptoAPI — this is complex." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  EASIEST: Use the manual DevTools method (see below)." -ForegroundColor Green

} else {
    Write-Fail "Could not extract token automatically."
}

# ─── Manual extraction fallback ─────────────────────────────────────
if (-not $accessToken) {
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Yellow
    Write-Host "  MANUAL TOKEN EXTRACTION" -ForegroundColor Yellow
    Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Open $Browser and go to https://chat.openai.com" -ForegroundColor White
    Write-Host "  2. Make sure you're logged in with your ChatGPT Plus account" -ForegroundColor White
    Write-Host "  3. Press F12 to open DevTools" -ForegroundColor White
    Write-Host "  4. Go to Application tab → Cookies → https://chat.openai.com" -ForegroundColor White
    Write-Host "  5. Find '__Secure-access_token'" -ForegroundColor White
    Write-Host "  6. Double-click the Value column, Ctrl+C to copy" -ForegroundColor White
    Write-Host ""
    
    $accessToken = Read-Host "Paste your __Secure-access_token here"
    
    if (-not $accessToken -or $accessToken.Length -lt 100) {
        Write-Fail "Token too short — make sure you copied the full value."
        exit 1
    }
    
    Write-Ok "Token received (length: $($accessToken.Length) chars)"
}

# ─── Step 3: Update accounts.json ──────────────────────────────────
Write-Step "Step 3: Updating webchat2api accounts.json"

# Find the accounts.json file
$wslAccountsPath = "${Webchat2ApiPath}/src/data/accounts.json"

# Try to find it via WSL
try {
    $testResult = wsl -e -c "cat $wslAccountsPath" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "accounts.json not found at $wslAccountsPath"
        exit 1
    }
} catch {
    Write-Fail "WSL command failed: $_"
    exit 1
}

# Build the new accounts.json via WSL Python
$updateScript = @"
import json
import sys
from datetime import datetime, timezone

accounts_path = r"$wslAccountsPath"
new_token = """$accessToken"""

try:
    with open(accounts_path, 'r') as f:
        accounts = json.load(f)
except FileNotFoundError:
    accounts = []

# Update the first account or create new one
if accounts:
    accounts[0]['access_token'] = new_token
    accounts[0]['status'] = '正常'
    accounts[0]['last_used_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    accounts[0]['success'] = 0
    accounts[0]['fail'] = 0
else:
    accounts.append({
        'access_token': new_token,
        'type': 'plus',
        'provider': 'gpt',
        'status': '正常',
        'quota': 120,
        'image_quota_unknown': False,
        'email': '',
        'user_id': '',
        'limits_progress': [],
        'default_model_slug': 'gpt-5-5-thinking',
        'restore_at': datetime.now(timezone.utc).isoformat(),
        'success': 0,
        'fail': 0,
        'last_used_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    })

with open(accounts_path, 'w') as f:
    json.dump(accounts, f, indent=2)

print("UPDATED")
"@

$updateResult = wsl -e -c "python3 -c '$updateScript'" 2>&1

if ($updateResult -match "UPDATED" -or $LASTEXITCODE -eq 0) {
    Write-Ok "accounts.json updated with new token"
} else {
    Write-Fail "Failed to update accounts.json: $updateResult"
    exit 1
}

# ─── Step 4: Restart webchat2api ───────────────────────────────────
if ($RestartProxy) {
    Write-Step "Step 4: Restarting webchat2api"
    
    # Kill existing webchat2api process
    wsl -e -c "pkill -f 'webchat2api.*main.py' || true" 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    
    # Start fresh
    wsl -e -c "cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py &" 2>&1 | Out-Null
    
    Write-Ok "webchat2api restarted on port $Webchat2ApiPort"
    Write-Host "  Waiting for initialization..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 8
    
    # Health check
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models" -TimeoutSec 5 -ErrorAction Stop
        Write-Ok "webchat2api health check passed — Oracle is ready!"
    } catch {
        Write-Warn "webchat2api may still be starting. Check with: curl http://127.0.0.1:$Webchat2ApiPort/v1/models"
    }
} else {
    Write-Step "Step 4: Skipping restart (use -RestartProxy to auto-restart)"
    Write-Host "  Restart manually: wsl -e -c 'cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py &'" -ForegroundColor DarkGray
}

# ─── Summary ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Token refresh complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Test Oracle: curl http://127.0.0.1:$Webchat2ApiPort/v1/models" -ForegroundColor DarkGray
Write-Host "    2. Or use the GPT-Oracle skill in Hermes" -ForegroundColor DarkGray
Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
