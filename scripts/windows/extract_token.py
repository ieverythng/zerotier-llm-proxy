#!/usr/bin/env python3
"""
Extract ChatGPT session token from Chrome cookies using browser_cookie3.
Updates webchat2api accounts.json with the fresh token.
"""

import json
import sys
import os
from datetime import datetime, timezone

# Add WSL path for accounts.json
WEBCHAT_ACCOUNTS = "/home/juanbeck/webchat2api/src/data/accounts.json"


def extract_token():
    """Extract __Secure-next-auth.session-token from Chrome."""
    try:
        import browser_cookie3
    except ImportError:
        print("ERROR: browser_cookie3 not installed. Run: pip install browser_cookie3")
        sys.exit(1)

    # Try Chrome first, then Edge
    for browser_fn, name in [(browser_cookie3.chrome, 'Chrome'), (browser_cookie3.edge, 'Edge')]:
        try:
            cookies = browser_fn(domain_name='chatgpt.com')
            if not cookies:
                cookies = browser_fn(domain_name='.chatgpt.com')
            
            for cookie in cookies:
                cname = cookie.name
                # Look for the session token
                if '__Secure-next-auth.session-token' in cname:
                    value = cookie.value
                    print(f"Found {cname} from {name} ({len(value)} chars)")
                    return value
        
        except Exception as e:
            print(f"{name}: {e}")
    
    # Also try without domain filter to find any chatgpt cookies
    for browser_fn, name in [(browser_cookie3.chrome, 'Chrome'), (browser_cookie3.edge, 'Edge')]:
        try:
            all_cookies = browser_fn()
            for cookie in all_cookies:
                if 'session-token' in cookie.name and 'chatgpt' in str(cookie.domain):
                    print(f"Found {cookie.name} @ {cookie.domain} from {name}")
                    return cookie.value
        except Exception as e:
            print(f"{name} (all cookies): {e}")
    
    return None


def update_accounts(token):
    """Update webchat2api accounts.json with new token."""
    # Use WSL path — need to run this from Windows so use forward slashes
    try:
        with open(WEBCHAT_ACCOUNTS, 'r') as f:
            accounts = json.load(f)
        
        print(f"\nUpdating webchat2api accounts.json...")
        print(f"  Account email: {accounts[0].get('email', 'unknown')}")
        
        accounts[0]['access_token'] = token
        accounts[0]['status'] = '\u6b63\u5e38'  # 正常 (normal)
        accounts[0]['last_used_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        with open(WEBCHAT_ACCOUNTS, 'w') as f:
            json.dump(accounts, f, indent=2)
        
        print("  accounts.json updated!")
        return True
    except Exception as e:
        print(f"ERROR updating accounts.json: {e}")
        return False


def main():
    print("=" * 50)
    print("  ChatGPT Token Extractor")
    print("=" * 50)
    print()
    
    token = extract_token()
    
    if not token:
        print("\nFAILED to extract session token.")
        print("\nTroubleshooting:")
        print("  1. Make sure you're logged into chatgpt.com in Chrome/Edge")
        print("  2. Close ALL browser windows before running this script")
        print("  3. Try opening chatgpt.com first, then run this script")
        sys.exit(1)
    
    print(f"\nToken extracted! Length: {len(token)} chars")
    print(f"Preview: {token[:50]}...")
    
    success = update_accounts(token)
    
    if success:
        print("\n" + "=" * 50)
        print("  SUCCESS! Token refreshed.")
        print("=" * 50)
        print("\nNext steps:")
        print("  1. Restart webchat2api proxy")
        print("  2. Test: curl http://localhost:9000/v1/models")
    else:
        print("\nToken extracted but accounts.json update failed.")
        print(f"Token saved to clipboard — paste it manually into:")
        print(f"  {WEBCHAT_ACCOUNTS}")


if __name__ == '__main__':
    main()
