# DCA Bot

Automated DCA (Dollar Cost Average) trading bot for the DCAS Calculator TradingView indicator.

When an alert fires, the bot screenshots your TradingView chart, uses Claude Vision AI to read the multiplier value printed on the chart, then executes a buy via Freqtrade on your chosen exchange. Stake size is automatically scaled: `BASE_STAKE × multiplier`.

## How it works

1. DCAS Calculator fires a weekly signal on TradingView
2. TradingView sends a webhook POST to `/dca-buy`
3. Bot calls the internal screenshot service to capture your chart
4. Screenshot is sent to Claude Vision API — it reads the blue multiplier label (e.g. x4.02) next to the latest green triangle signal
5. Stake is calculated: `BASE_STAKE × multiplier` (e.g. $150 × 4.02 = $603)
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

- Docker and Docker Compose
- Unraid server (or any Linux host)
- TradingView **paid account** (Essential or higher) — required for webhook alerts and to access the DCAS Calculator which is a paid invite-only indicator
- Account on a supported exchange with API key (no withdrawal permission needed)
- Anthropic API key (Claude Vision — cheap, ~$0.01 per screenshot)
- Telegram bot (optional, for buy notifications)

### 2. Clone and prepare

```bash
git clone https://github.com/YOUR_USERNAME/dca-bot.git /mnt/user/appdata/dcas
cd /mnt/user/appdata/dcas
mkdir -p user_data/strategies user_data/logs data
cp config.json user_data/config.json
cp strategies/DCAStrategy.py user_data/strategies/
cp .env.example .env
```

### 3. Choose your exchange

The bot works with any exchange supported by Freqtrade. Set `exchange.name` in `user_data/config.json` to the exchange ID from the table below, and set `stake_currency` to match what your exchange uses.

| Exchange          | exchange.name   | AUD pairs | USDT pairs | Notes                        |
|-------------------|-----------------|-----------|------------|------------------------------|
| Kraken            | `kraken`        | ✅        | ✅         | Best for AUD trading         |
| Binance           | `binance`       | ❌        | ✅         | Largest liquidity globally   |
| Binance US        | `binanceus`     | ❌        | ✅         | US residents only            |
| Coinbase Advanced | `coinbase`      | ✅        | ✅         | Good AUD support             |
| Bybit             | `bybit`         | ❌        | ✅         | Popular, low fees            |
| OKX               | `okx`           | ❌        | ✅         |                              |
| KuCoin            | `kucoin`        | ❌        | ✅         |                              |
| Gate.io           | `gateio`        | ❌        | ✅         |                              |
| HTX (Huobi)       | `htx`           | ❌        | ✅         |                              |
| Bitfinex          | `bitfinex`      | ❌        | ✅         |                              |

> For Australian traders, **Kraken** and **Coinbase Advanced** are the main options with AUD pairs. For global traders using USDT, **Binance** or **Bybit** are the most popular choices.

For the full list of supported exchanges run: `docker run --rm freqtradeorg/freqtrade:stable list-exchanges`

### 4. Edit user_data/config.json

Fill in your exchange API credentials and Freqtrade settings:
- `exchange.name` — exchange ID from the table above (e.g. `kraken`, `binance`)
- `exchange.key` — your exchange API key
- `exchange.secret` — your exchange API secret
- `stake_currency` — the currency you're buying with (e.g. `AUD`, `USDT`)
- `pair_whitelist` — pairs to trade, using your exchange's format (e.g. `["BTC/AUD", "SOL/AUD"]` on Kraken or `["BTC/USDT", "SOL/USDT"]` on Binance)
- `api_server.password` — password to protect the Freqtrade API (pick anything)
- `api_server.jwt_secret_key` — run `openssl rand -hex 32` and paste the result

> **Security:** Create a dedicated API key for this bot on your exchange. Enable only **Query** and **Create & Modify Orders** (or equivalent). Never enable withdrawal permissions. If your exchange supports IP whitelisting, add your server's IP address.

### 4. Edit .env

Fill in all `CHANGE_THIS` values:

```env
SECRET_TOKEN=        # run: openssl rand -hex 24
FREQTRADE_PASS=      # must match api_server.password in config.json
ANTHROPIC_API_KEY=   # from https://console.anthropic.com
BASE_STAKE=150       # AUD per buy (multiplied by chart multiplier)

TV_CHART_URL_BTC=    # your BTC/AUD chart URL (see TradingView setup below)
TV_CHART_URL_SOL=    # your SOL/AUD chart URL
TV_COOKIES_JSON=     # exported session cookies (see below)

TELEGRAM_TOKEN=      # optional
TELEGRAM_CHAT_ID=    # optional
```

### 5. TradingView chart setup

The bot reads the multiplier by screenshotting your TradingView chart. The chart layout needs to be set up correctly so the latest signal label is always clearly visible.

**Add the indicator:**
1. Open TradingView and load your BTC/AUD chart
2. Add the **DCAS Calculator** indicator (search in the indicator library)
3. The indicator will show green triangle buy signals with blue multiplier labels (e.g. x4.02) next to each signal

**Move the indicator to its own pane:**

This is the most important step. By default the indicator overlays on the price chart, which means the multiplier labels can get hidden behind the orange/blue trend bands when price is high. Moving it to a separate pane ensures the labels are always clearly visible on a clean dark background regardless of price movement.

1. Click the indicator name in the top-left of the chart
2. Click the **Move to new pane** icon (two overlapping squares with an arrow)
3. The indicator will now appear in its own panel below the price chart, showing only the green triangles and blue multiplier labels against a dark background

**Zoom in:**

Zoom the chart in to show only the last few weeks of data. This makes the labels larger and easier for the Vision AI to read accurately. You do not need to scroll the chart — TradingView automatically advances to the current date every time the chart is loaded.

**Save the layout:**

Click the save icon (or Ctrl+S) to save your layout. The URL in your browser (e.g. `https://www.tradingview.com/chart/AbCdEfGh/`) is your `TV_CHART_URL_BTC`. Copy it into your `.env` — you only need to do this once, the URL never changes when you save.

**Set up a separate chart for each coin** (e.g. SOL/AUD) following the same steps, and add each URL to the corresponding `TV_CHART_URL_XXX` variable in `.env`.

### 6. Export TradingView cookies

The bot uses your existing TradingView session to load the chart, so your 2FA is never touched.

1. Log into TradingView in your browser (make sure you're fully logged in)
2. Install the **Cookie-Editor** browser extension
3. Go to `tradingview.com`
4. Open Cookie-Editor and click **Export** — this copies the cookies JSON to your clipboard
5. On your Unraid server, paste the JSON into a file and compact it to a single line:

```bash
cat > /tmp/cookies.json   # paste the JSON, then press Ctrl+D
jq -c . /tmp/cookies.json # outputs a single-line version — copy this
```

6. Paste the single-line output as the value of `TV_COOKIES_JSON` in your `.env`

> **Note:** TradingView session cookies expire periodically (typically every few months). When the bot logs `TradingView session expired`, repeat this process and restart the screenshot service: `docker compose restart screenshot-service`

### 7. Start

```bash
docker compose up -d
docker compose ps   # all three containers should show as Running
```

Check the bot is healthy:
```bash
curl http://localhost:8200/health
# {"status":"ok"}
```

### 8. Set up TradingView alerts

Create one alert per coin on its chart:

1. Open your BTC/AUD chart in TradingView
2. Click the **Alerts** button (clock icon in the toolbar)
3. Set **Condition** to the DCAS Calculator signal
4. Set **Trigger** to **Once Per Bar Close** (weekly timeframe)
5. Under **Notifications**, enable **Webhook URL** and enter:
```
http://YOUR_UNRAID_IP:8200/dca-buy?token=YOUR_SECRET_TOKEN
```
6. Set the **Message** body to:
```json
{"pair": "BTC/AUD"}
```
7. Save the alert

Repeat for SOL/AUD with `{"pair": "SOL/AUD"}` as the message body.

> **Auth:** The token goes in the URL as `?token=...`, not in the message body.

### 9. Test

Fire a manual test to confirm the full pipeline works:

```bash
curl -s -X POST "http://localhost:8200/dca-buy?token=YOUR_SECRET_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pair": "BTC/AUD"}'
```

Check the logs:
```bash
docker compose logs dca-bot --tail=20
```

You should see: screenshot captured → Vision extracted multiplier → BUY success.

### 10. Go live

When happy with dry run results:

1. Set `"dry_run": false` in `user_data/config.json`
2. Restart freqtrade: `docker compose restart freqtrade`

## Dashboard

Open in your browser:
```
http://YOUR_UNRAID_IP:8200/dashboard?token=YOUR_SECRET_TOKEN
```

Shows total buys, total invested, average and max multipliers, and a full buy history table. Auto-refreshes every 60 seconds.

## Multiple coins

Add pairs to `pair_whitelist` in `user_data/config.json`:
```json
"pair_whitelist": ["BTC/AUD", "SOL/AUD"]
```

Create a separate TradingView chart and alert for each coin. Each alert sends a different pair in the webhook body — the bot automatically loads the correct chart URL for that pair using the `TV_CHART_URL_XXX` variable in `.env`.

## Position sizing

| BASE_STAKE | Multiplier | Stake  |
|------------|------------|--------|
| $150       | ×1.0       | $150   |
| $150       | ×2.5       | $375   |
| $150       | ×4.02      | $603   |

## Security

- Token authentication on all endpoints (`?token=` query param or `X-Token` header)
- All secrets in `.env` — never hardcoded
- `.env` and `user_data/config.json` are gitignored
- Screenshot service has no external ports — internal Docker network only
- Exchange API: no withdrawal permissions, IP whitelisted to your server where possible
- GitHub repo: set to private
- TradingView 2FA stays on — cookies are used instead of login credentials

## ⚠️ Disclaimer

For educational and informational purposes only. Not financial advice.
Trading involves significant risk of loss. Use at your own risk.
