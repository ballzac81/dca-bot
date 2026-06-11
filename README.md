# DCA Bot

Automated DCA (Dollar Cost Average) trading bot for the DCAS Calculator TradingView indicator.

When an alert fires, the bot screenshots your TradingView chart, uses Claude Vision AI to read the multiplier value printed on the chart, then executes a buy via Freqtrade on your chosen exchange. Stake size is automatically scaled: `BASE_STAKE × multiplier`.

## How it works

1. DCAS Calculator fires a signal on TradingView
2. TradingView sends a webhook POST to `/dca-buy`
3. Bot calls the internal screenshot service to capture your chart
4. Screenshot is sent to Claude Vision API — it reads the blue multiplier label (e.g. x4.02) next to the latest green triangle signal
5. Stake is calculated: `BASE_STAKE × multiplier` (e.g. 100 × 4.02 = 402)
6. Freqtrade executes a `forcebuy` on your exchange for that stake amount
7. Buy is logged to SQLite and a Telegram notification is sent
8. Dashboard at `:8200/dashboard` shows all buy history

## Services

| Container       | Port | Purpose                                      |
|-----------------|------|----------------------------------------------|
| DCA_bot         | 8200 | Webhook receiver, dashboard, buy log         |
| DCA_freqtrade   | 8201 | Freqtrade — executes trades on your exchange |
| DCA_screenshot  | —    | Internal only — Playwright screenshot service|

The screenshot service has no external port — it is only reachable from inside the Docker network.

## Setup

### 1. Prerequisites

- Docker and Docker Compose (any Linux, macOS, or Windows host)
- TradingView **paid account** (Essential or higher) — required for webhook alerts and to access the DCAS Calculator, which is a paid invite-only indicator
- Account on a supported exchange with API key (no withdrawal permission needed)
- Anthropic API key (Claude Vision — cheap, ~$0.01 per screenshot)
- Telegram bot (optional, for buy notifications)

### 2. Clone and prepare

```bash
git clone https://github.com/YOUR_USERNAME/dca-bot.git dca-bot
cd dca-bot
mkdir -p user_data/strategies user_data/logs data
cp config.json user_data/config.json
cp strategies/DCAStrategy.py user_data/strategies/
cp .env.example .env
```

> You can clone to any directory you like, e.g. `/opt/dca-bot`, `~/dca-bot`, or wherever you keep Docker projects on your host.

### 3. Choose your exchange

The bot works with any exchange supported by Freqtrade. Set `exchange.name` in `user_data/config.json` to the exchange ID from the table below, and set `stake_currency` to match what you are buying with.

| Exchange          | exchange.name   | Fiat pairs (AUD/EUR/GBP) | USDT pairs | Notes                      |
|-------------------|-----------------|--------------------------|------------|----------------------------|
| Kraken            | `kraken`        | ✅                       | ✅         | Wide fiat support          |
| Binance           | `binance`       | ❌                       | ✅         | Largest liquidity globally |
| Binance US        | `binanceus`     | ❌                       | ✅         | US residents only          |
| Coinbase Advanced | `coinbase`      | ✅                       | ✅         | Good fiat support          |
| Bybit             | `bybit`         | ❌                       | ✅         | Popular, low fees          |
| OKX               | `okx`           | ❌                       | ✅         |                            |
| KuCoin            | `kucoin`        | ❌                       | ✅         |                            |
| Gate.io           | `gateio`        | ❌                       | ✅         |                            |
| HTX (Huobi)       | `htx`           | ❌                       | ✅         |                            |
| Bitfinex          | `bitfinex`      | ❌                       | ✅         |                            |

> For the full list of supported exchanges run: `docker run --rm freqtradeorg/freqtrade:stable list-exchanges`

### 4. Edit user_data/config.json

Fill in your exchange API credentials and Freqtrade settings:

- `exchange.name` — exchange ID from the table above (e.g. `kraken`, `binance`)
- `exchange.key` — your exchange API key
- `exchange.secret` — your exchange API secret
- `stake_currency` — the currency you are buying with (e.g. `USDT`, `AUD`, `EUR`)
- `pair_whitelist` — pairs to trade in your exchange's format (e.g. `["BTC/USDT", "SOL/USDT"]` or `["BTC/AUD", "SOL/AUD"]`)
- `api_server.password` — password to protect the Freqtrade API (choose anything secure)
- `api_server.jwt_secret_key` — run `openssl rand -hex 32` and paste the result

> **Security:** Create a dedicated API key for this bot on your exchange. Enable only **Query** and **Create & Modify Orders** (or your exchange's equivalent). Never enable withdrawal permissions. If your exchange supports IP whitelisting, restrict the key to your server's IP address.

### 5. Edit .env

Copy `.env.example` to `.env` and fill in all `CHANGE_THIS` values:

```env
SECRET_TOKEN=        # run: openssl rand -hex 24
FREQTRADE_PASS=      # must match api_server.password in config.json
ANTHROPIC_API_KEY=   # from https://console.anthropic.com
BASE_STAKE=100       # amount per buy signal, in your stake currency (multiplied by the chart multiplier)

# Per-pair chart URLs — the bot picks the right chart based on the alert pair.
# TV_CHART_URL is the fallback used if no per-pair URL matches.
TV_CHART_URL_BTC=    # URL of your BTC chart (see TradingView setup below)
TV_CHART_URL_SOL=    # URL of your SOL chart (add more pairs as needed)
TV_CHART_URL=        # fallback / single-pair setups

TV_COOKIES_JSON=     # exported session cookies (see below)

TELEGRAM_TOKEN=      # optional
TELEGRAM_CHAT_ID=    # optional
```

The `TV_CHART_URL_XXX` variable name is derived from the base currency of the pair. For example, a `BTC/USDT` alert uses `TV_CHART_URL_BTC`. Add one entry per coin you want to trade.

### 6. TradingView chart setup

The bot reads the multiplier by screenshotting your TradingView chart. The chart needs to be set up correctly so the latest signal label is always clearly visible.

**Add the indicator:**

1. Open TradingView and load a chart for your chosen pair (e.g. BTC/USDT)
2. Add the **DCAS Calculator** indicator (search in the indicator library — you must have access to it)
3. The indicator shows green triangle buy signals with blue multiplier labels (e.g. x4.02) next to each signal

**Move the indicator to its own pane:**

This is the most important step. By default the indicator overlays on the price chart, which means the multiplier labels can get hidden behind the trend bands as price moves. Moving it to a separate pane ensures the labels are always clearly visible on a clean dark background, regardless of price.

1. Click the indicator name in the top-left of the chart
2. Click the **Move to new pane** icon (two overlapping squares with an arrow)
3. The indicator now appears in its own panel below the price chart, showing only the green triangles and blue multiplier labels against a dark background

**Zoom in:**

Zoom the chart in to show the last few weeks of data. This makes the labels larger and easier for Vision AI to read accurately. You do not need to manually scroll the chart — TradingView automatically advances to the current date each time the chart is loaded.

**Save the layout:**

Click the save icon (or Ctrl+S) to save your layout. The URL in your browser (e.g. `https://www.tradingview.com/chart/AbCdEfGh/`) is your chart URL. Copy it into the relevant `TV_CHART_URL_XXX` variable in your `.env`. You only need to do this once — the URL does not change after saving.

**Repeat for each coin** you want to trade, setting up a separate saved chart for each and adding its URL to `.env`.

### 7. Export TradingView cookies

The bot uses your existing TradingView session to load the chart, so your password and 2FA are never touched.

> ⚠️ **Keep your cookies private.** These cookies give full access to your TradingView account — anyone who has them can log in as you. Never share them, never commit them to git, and never paste them anywhere other than your own `.env` file on your own server.

1. Log into TradingView in your browser (make sure you are fully logged in)
2. Install the **Cookie-Editor** browser extension
3. Navigate to `tradingview.com`
4. Open Cookie-Editor and click **Export** — this copies the cookies JSON to your clipboard

The exported JSON is a multi-line array. The `.env` file requires it on a **single line**. Use one of the following methods:

**Linux / macOS:**
```bash
# Paste the JSON into a temp file, then press Ctrl+D:
cat > /tmp/cookies.json

# Output as a single line:
jq -c . /tmp/cookies.json
```

**Windows (PowerShell):**
```powershell
Get-Content C:\path\to\cookies.json | ConvertFrom-Json | ConvertTo-Json -Compress -Depth 10
```

**Online (no tools needed):**
Go to [jsonformatter.org](https://jsonformatter.org/) → paste the JSON → click **Minify** → copy the result.

5. Paste the single-line JSON as the value of `TV_COOKIES_JSON` in your `.env`

> **Note:** TradingView session cookies expire periodically (typically every few months). When the bot logs `TradingView session expired`, repeat this process and restart the screenshot service: `docker compose restart screenshot-service`

### 8. Start

```bash
docker compose up -d
docker compose ps   # all three containers should show as Running
```

Check the bot is healthy:
```bash
curl http://localhost:8200/health
# {"status":"ok"}
```

### 9. Set up TradingView alerts

Create one alert per coin on its chart:

1. Open the chart for your first coin in TradingView
2. Click the **Alerts** button (clock icon in the toolbar)
3. Set **Condition** to the DCAS Calculator signal
4. Set **Trigger** to **Once Per Bar Close** (on the timeframe the indicator uses)
5. Under **Notifications**, enable **Webhook URL** and enter:
```
http://YOUR_SERVER_IP:8200/dca-buy?token=YOUR_SECRET_TOKEN
```
6. Set the **Message** body to:
```json
{"pair": "BTC/USDT"}
```
Replace `BTC/USDT` with the actual pair for that chart, using the exact format your exchange uses (e.g. `BTC/AUD`, `BTC/USDT`, `SOL/USDT`).

7. Save the alert

Repeat for each additional coin with its corresponding pair in the message body.

> **Auth:** The token goes in the URL as `?token=...`, not in the message body. The message body must be valid JSON containing only the `pair` key.

### 10. Test

Fire a manual test to confirm the full pipeline works end-to-end:

```bash
curl -s -X POST "http://localhost:8200/dca-buy?token=YOUR_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pair": "BTC/USDT"}'
```

Replace `BTC/USDT` with one of your configured pairs. Check the logs:
```bash
docker compose logs dca-bot --tail=20
```

You should see: screenshot captured → Vision extracted multiplier → BUY executed.

### 11. Go live

The bot runs in dry-run mode by default (no real trades). When you are satisfied with dry-run results:

1. Set `"dry_run": false` in `user_data/config.json`
2. Restart freqtrade: `docker compose restart freqtrade`

## Dashboard

Open in your browser:
```
http://YOUR_SERVER_IP:8200/dashboard?token=YOUR_SECRET_TOKEN
```

Shows total buys, total invested, average and max multipliers, and a full buy history table. Auto-refreshes every 60 seconds.

## Multiple coins

Add all pairs to `pair_whitelist` in `user_data/config.json`:
```json
"pair_whitelist": ["BTC/USDT", "SOL/USDT", "SOL/USDT"]
```

Create a separate saved TradingView chart and alert for each coin. Each alert sends a different pair in the webhook body — the bot automatically loads the correct chart URL for that pair using the matching `TV_CHART_URL_XXX` variable in `.env`.

## Position sizing

The stake per buy is `BASE_STAKE × multiplier`. Example with `BASE_STAKE=100`:

| Multiplier | Stake |
|------------|-------|
| ×1.0       | 100   |
| ×2.5       | 250   |
| ×4.02      | 402   |

The multiplier is read directly from the DCAS Calculator label on the chart by Claude Vision.

## Security

- Token authentication on all endpoints (`?token=` query param or `X-Token` header)
- All secrets in `.env` — never hardcoded
- `.env` and `user_data/config.json` are gitignored and never committed
- Screenshot service has no external ports — accessible only from inside the Docker network
- Exchange API: no withdrawal permissions, IP whitelist to your server where possible
- TradingView 2FA stays on — cookies are used to authenticate, not your password
- TradingView cookies are stored only in your local `.env` file — never share them

## ⚠️ Disclaimer

For educational and informational purposes only. Not financial advice.
Trading involves significant risk of loss. Use at your own risk.
