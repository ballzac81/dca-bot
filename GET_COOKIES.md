# How to export your TradingView session cookies

This lets the bot access your TradingView chart without logging in,
so your 2FA stays enabled and the bot never touches your password.

## Steps

1. **Install Cookie-Editor** browser extension
   - Chrome: https://chrome.google.com/webstore/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
   - Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookie-editor/

2. **Log into TradingView** normally in your browser (complete your 2FA as usual)

3. **Navigate to your chart** — the one with the IA-DCA-Calculator indicator loaded,
   at the correct timeframe (e.g. 1D BTC/AUD)

4. **Open Cookie-Editor** — click the extension icon in your toolbar

5. Click **Export** → **Export as JSON**
   - This copies a JSON array of all TradingView cookies to your clipboard

6. **Paste into your .env file** as the value for `TV_COOKIES_JSON`:
   ```
   TV_COOKIES_JSON=[{"name":"sessionid","value":"abc123...","domain":".tradingview.com",...}]
   ```
   Make sure it's all on one line.

7. Also copy your **chart URL** from the browser address bar and set `TV_CHART_URL`.

## When do cookies expire?

TradingView sessions typically last several weeks. When they expire the bot will:
- Log the error
- Send you a Telegram message: "TradingView session expired — update TV_COOKIES_JSON"

To refresh: repeat steps 2–6 above, update `.env`, then run:
```bash
docker compose restart screenshot-service
```

## Security notes

- Your .env file is listed in .gitignore — it will never be accidentally committed to git
- Cookie-Editor only exports cookies for the current site (tradingview.com)
- These cookies only grant access to your TradingView account, not your exchange
- Your 2FA, password, and Kraken account are completely unaffected
