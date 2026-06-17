# Extract-ChatGPTToken.ps1
# =============================================
# Extracts __Secure-next-auth.session-token from Chrome cookies,
# decrypts using DPAPI, and updates webchat2api accounts.json.
#
# Prerequisites:
#   - Chrome must be CLOSED (cookie DB is locked while running)
#   - You must be logged into chatgpt.com in Chrome
#   - Python 3 with 'cryptography' package installed
#
# Usage:
#   .\Extract-ChatGPTToken.ps1
#   .\Extract-ChatGPTToken.ps1 -RestartProxy
# =============================================

param(
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
Write-Host "║     ChatGPT Token Extractor — DPAPI Decrypt         ║" -ForegroundColor Magenta
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Magenta

# ─── Step 1: Check Chrome is closed ────────────────────────────────
Write-Step "Checking Chrome status"

$chromeProcesses = Get-Process -Name "chrome" -ErrorAction SilentlyContinue
if ($chromeProcesses) {
    Write-Warn "Chrome is running — cookie DB may be locked."
    $close = Read-Host "Close Chrome now and press Y? (Y/N)"
    if ($close -eq 'Y') {
        $chromeProcesses | Stop-Process -Force
        Start-Sleep -Seconds 2
        Write-Ok "Chrome closed"
    } else {
        Write-Warn "Proceeding anyway — decryption may fail if DB is locked."
    }
}

# ─── Step 2: Install dependencies if needed ────────────────────────
Write-Step "Checking Python dependencies"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
}

if (-not $pythonCmd) {
    Write-Fail "Python not found. Install Python 3.10+ and add to PATH."
    exit 1
}

# Check for cryptography package
try {
    & python -c "from cryptography.hazmat.primitives.ciphers import Cipher; print('OK')" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "'cryptography' package not installed. Installing..."
        & python -m pip install cryptography
    }
    Write-Ok "Python + cryptography ready"
} catch {
    Write-Warn "Installing cryptography package..."
    & python -m pip install cryptography
}

# ─── Step 3: Extract and decrypt token ─────────────────────────────
Write-Step "Extracting session token from Chrome cookies"

$cookieDb = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Network\cookies"

if (-not (Test-Path $cookieDb)) {
    Write-Fail "Cookie DB not found at: $cookieDb"
    exit 1
}

$pythonScript = @"
import sqlite3
import json
import base64
import struct
import sys
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

def decrypt_dpapi(encrypted_bytes):
    """Decrypt using Windows DPAPI via ctypes."""
    import ctypes
    from ctypes import wintypes
    
    advapi32 = ctypes.WinDLL('advapi32')
    
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ('cbData', wintypes.DWORD),
            ('pbData', ctypes.c_void_p),
        ]
    
    CryptUnprotectData = advapi32.CryptUnprotectData
    CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    CryptUnprotectData.restype = wintypes.BOOL
    
    crypt32 = ctypes.WinDLL('crypt32')
    
    in_blob = DATA_BLOB()
    in_blob.cbData = len(encrypted_bytes)
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(encrypted_bytes, len(encrypted_bytes)), ctypes.c_void_p)
    
    out_blob = DATA_BLOB()
    out_blob.cbData = 0
    out_blob.pbData = 0
    
    result = CryptUnprotectData(
        ctypes.pointer(in_blob), None, None, None, None, 0,
        ctypes.pointer(out_blob)
    )
    
    if result and out_blob.cbData > 0:
        decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        crypt32.LocalFree(out_blob.pbData)
        return decrypted
    return None

def get_master_key():
    """Get Chrome's master key from Local State."""
    local_state_path = os.path.join(os.environ['LOCALAPPDATA'], 
                                     'Google', 'Chrome', 'User Data', 'Local State')
    
    with open(local_state_path, 'r') as f:
        local_state = json.load(f)
    
    encrypted_key = base64.b64decode(local_state['encrypted_key'])
    master_key = decrypt_dpapi(encrypted_key)
    
    if master_key and master_key.startswith(b'v10'):
        master_key = master_key[3:]
    
    return master_key

def decrypt_cookie(encrypted_bytes, master_key):
    """Decrypt a v10 cookie using AES-128-GCM."""
    if not encrypted_bytes.startswith(b'v10'):
        return None
    
    data = encrypted_bytes[3:]  # skip 'v10' prefix
    
    if len(data) < 28:
        return None
    
    iv = data[:12]
    ciphertext_and_tag = data[12:]
    
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]
    
    cipher = Cipher(
        algorithms.AES(master_key),
        modes.GCM(iv, tag),
        backend=default_backend()
    )
    
    decryptor = cipher.decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    
    return plaintext

# Main extraction
cookie_db = r"$cookieDb"

try:
    conn = sqlite3.connect(cookie_db)
    cursor = conn.cursor()
    
    # Try both cookie name variants
    for cookie_name in ['__Secure-next-auth.session-token.0', '__Secure-next-auth.session-token']:
        cursor.execute("""
            SELECT encrypted_value FROM cookies 
            WHERE name = ? AND host_key = '.chatgpt.com'
        """, (cookie_name,))
        
        row = cursor.fetchone()
        if row:
            encrypted = row[0]
            
            master_key = get_master_key()
            if not master_key:
                print("ERROR:Could not decrypt master key")
                sys.exit(1)
            
            decrypted = decrypt_cookie(encrypted, master_key)
            if decrypted:
                token = decrypted.decode('utf-8')
                print(f"TOKEN:{token}")
                sys.exit(0)
            else:
                print(f"ERROR:Decryption failed for {cookie_name}")
    
    conn.close()
    print("ERROR:Session token cookie not found — are you logged into chatgpt.com?")
    sys.exit(1)

except sqlite3.OperationalError as e:
    if "database is locked" in str(e):
        print("ERROR:Chrome is still running — close it and retry")
    else:
        print(f"ERROR:{e}")
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{e}")
    sys.exit(1)
"@

$result = & python -c $pythonScript 2>&1

if ($result -match "^TOKEN:(.+)$") {
    $accessToken = $matches[1]
    Write-Ok "Token extracted! (length: $($accessToken.Length) chars)"
    Write-Host "  Preview: $($accessToken.Substring(0, [Math]::Min(60, $accessToken.Length)))..." -ForegroundColor DarkGray
} else {
    Write-Fail "Extraction failed: $result"
    exit 1
}

# ─── Step 4: Update webchat2api accounts.json ──────────────────────
Write-Step "Updating webchat2api accounts.json"

$wslAccountsPath = "${Webchat2ApiPath}/src/data/accounts.json"

$updateScript = @"
import json
from datetime import datetime, timezone

accounts_path = r"$wslAccountsPath"
new_token = """$accessToken"""

with open(accounts_path, 'r') as f:
    accounts = json.load(f)

accounts[0]['access_token'] = new_token
accounts[0]['status'] = '正常'
accounts[0]['last_used_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
accounts[0]['success'] = 0
accounts[0]['fail'] = 0

with open(accounts_path, 'w') as f:
    json.dump(accounts, f, indent=2)

print("OK")
"@

$updateResult = wsl -e -c "python3 -c `"$updateScript`"" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Ok "accounts.json updated"
} else {
    Write-Fail "Failed to update accounts.json: $updateResult"
    exit 1
}

# ─── Step 5: Restart webchat2api ────────────────────────────────────
if ($RestartProxy) {
    Write-Step "Restarting webchat2api"
    
    wsl -e -c "pkill -f 'webchat2api.*main.py' || true" 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    
    wsl -e -c "cd ${Webchat2ApiPath}/src && PORT=${Webchat2ApiPort} .venv/bin/python main.py &" 2>&1 | Out-Null
    
    Write-Ok "webchat2api restarted"
    Start-Sleep -Seconds 8
    
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Webchat2ApiPort/v1/models" -TimeoutSec 5 -ErrorAction Stop
        Write-Ok "Health check passed — Oracle is ready!"
        
        # Show available models
        if ($health.data) {
            Write-Host "`n  Available GPT models:" -ForegroundColor DarkGray
            foreach ($model in $health.data) {
                Write-Host "    - $($model.id)" -ForegroundColor DarkGray
            }
        }
    } catch {
        Write-Warn "webchat2api still starting — retry health check in 10s"
    }
}

# ─── Summary ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Token refresh complete!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
