# Extract ChatGPT Access Token from Browser

## Method: Browser Console (Fastest)

1. Go to **chat.openai.com** and make sure you're logged in
2. Press **F12** to open DevTools
3. Go to the **Console** tab
4. Paste and run this JavaScript:

```javascript
// Extract ChatGPT access token
(function() {
  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, ...valueParts] = cookie.trim().split('=');
    if (name.includes('access_token')) {
      console.log('=== YOUR ACCESS TOKEN ===');
      console.log(valueParts.join('='));
      console.log('=========================');
      navigator.clipboard.writeText(valueParts.join('='));
      console.log('Copied to clipboard!');
      return;
    }
  }
  
  // Fallback: check __Secure-access_token via cookie jar
  const secureCookies = document.cookie.split(';').filter(c => c.includes('Secure'));
  if (secureCookies.length > 0) {
    console.log('Secure cookies found:', secureCookies);
  } else {
    console.log('No access token found in document.cookie. Try Method 2 below.');
  }
})();
```

> **Note:** `__Secure-*` cookies have the Secure flag and may not appear in `document.cookie` if the page loaded over HTTP. If the above doesn't work:

## Method 2: Application Tab (More Reliable)

1. Go to **chat.openai.com**
2. Press **F12** → **Application** tab
3. Expand **Cookies** → `https://chat.openai.com`
4. Find `__Secure-access_token`
5. Double-click the **Value** column to select all, then **Ctrl+C** to copy
6. Paste into webchat2api's `data/accounts.json`

## Method 3: Network Tab (If Above Fail)

1. Go to **chat.openai.com**
2. Press **F12** → **Network** tab
3. Refresh the page (F5)
4. Click on any request to `chat.openai.com`
5. Go to **Headers** → **Cookies** section
6. Find `__Secure-access_token` value

## Using the Token in webchat2api

Update `data/accounts.json`:

```json
[
  {
    "access_token": "paste_your_token_here",
    "type": "plus",
    "provider": "gpt",
    "status": "正常",
    "email": "your@email.com"
  }
]
```

Then restart webchat2api: `PORT=9000 .venv/bin/python main.py`

## Token Lifespan

- Access tokens typically last **7-30 days** before OpenAI rotates them
- Logging out/in on chat.openai.com invalidates existing tokens
- When you see "密钥无效或已失效", repeat this extraction process
