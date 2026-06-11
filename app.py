#!/usr/bin/env python3
"""DCA Bot — Webhook receiver for the DCAS Calculator indicator.

Flow:
  1. TradingView alert fires -> POST /dca-buy
  2. Bot calls screenshot-service to capture the TradingView chart
  3. Screenshot sent to Claude Vision API -> multiplier extracted
  4. Buy executed: BASE_STAKE * multiplier via Freqtrade -> Kraken

Endpoints:
  POST /dca-buy   DCA entry signal
  GET  /status    open trade info + free balance
  GET  /health    health check
"""

import logging
import os
import re
import time
import base64
import requests
import anthropic
from functools import wraps
from flask import Flask, request, jsonify, render_template_string
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import db

# -- Pair validation ----------------------------------------------------------
PAIR_RE = re.compile(r'^[A-Z0-9]+/[A-Z0-9]+$')

def valid_pair(pair: str) -> bool:
    return bool(PAIR_RE.match(pair))

# -- Config -------------------------------------------------------------------
SECRET_TOKEN        = os.environ.get("SECRET_TOKEN", "")
TRADING_PAIR        = os.environ.get("TRADING_PAIR", "BTC/AUD")
FREQTRADE_API       = os.environ.get("FREQTRADE_API", "http://freqtrade:8080/api/v1")
FREQTRADE_USER      = os.environ.get("FREQTRADE_USER", "admin")
FREQTRADE_PASS      = os.environ.get("FREQTRADE_PASS", "")
TG_TOKEN            = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT             = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
SCREENSHOT_URL      = os.environ.get("SCREENSHOT_URL", "http://screenshot-service:5001")

# Per-pair chart URLs — e.g. TV_CHART_URL_BTC=https://... TV_CHART_URL_SOL=https://...
# Falls back to TV_CHART_URL if no per-pair URL is set.
def get_chart_url(pair: str) -> str:
    key = "TV_CHART_URL_" + pair.split("/")[0].upper()
    return os.environ.get(key) or os.environ.get("TV_CHART_URL", "")

BASE_STAKE          = float(os.environ.get("BASE_STAKE", "150"))
MIN_STAKE           = float(os.environ.get("MIN_STAKE", "15.0"))

API_RETRIES         = int(os.environ.get("API_RETRIES", "3"))
API_RETRY_DELAY     = float(os.environ.get("API_RETRY_DELAY", "5.0"))
API_TIMEOUT         = int(os.environ.get("API_TIMEOUT", "15"))

# -- Logging ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# -- Startup validation -------------------------------------------------------
def _validate_config():
    """Fail fast if required environment variables are missing."""
    required = {
        "SECRET_TOKEN":      SECRET_TOKEN,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "FREQTRADE_PASS":    FREQTRADE_PASS,
    }
    missing = [k for k, v in required.items() if not v or v.startswith("CHANGE_THIS")]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        logger.error("Check your .env file and restart the container.")
        raise SystemExit(1)

_validate_config()

# -- Flask --------------------------------------------------------------------
app = Flask(__name__)
db.init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# -- Telegram -----------------------------------------------------------------
def telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=5,
        )
    except Exception as e:
        logger.warning("Telegram failed: %s", e)

# -- Freqtrade API ------------------------------------------------------------
def _ft_request(method: str, endpoint: str, **kwargs) -> dict:
    url = f"{FREQTRADE_API}/{endpoint.lstrip('/')}"
    auth = (FREQTRADE_USER, FREQTRADE_PASS)
    last_error = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            resp = requests.request(method, url, auth=auth, timeout=API_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            logger.warning("Freqtrade API attempt %d/%d failed: %s", attempt, API_RETRIES, e)
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
    raise RuntimeError(f"Freqtrade API failed after {API_RETRIES} attempts: {last_error}")

def get_open_trade(pair: str):
    data = _ft_request("GET", "/status")
    trades = [t for t in data if t["pair"] == pair and t["is_open"]]
    return sorted(trades, key=lambda t: t["open_date"])[-1] if trades else None

def get_free_balance() -> float:
    data = _ft_request("GET", "/balance")
    return float(data.get("available_capital", data.get("total", 0)))

# -- Screenshot + Vision ------------------------------------------------------
def crop_right(screenshot_b64: str, keep_pct: float = 0.50) -> str:
    """Crop to the rightmost portion of the chart where the latest signal lives.
    Also crops vertically to the middle pane (indicator labels area).
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64)))
        w, h = img.size
        left   = int(w * (1 - keep_pct))
        top    = int(h * 0.30)   # skip top price chart pane
        bottom = int(h * 0.75)   # skip bottom DCAS-Model pane
        cropped = img.crop((left, top, w, bottom))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning("Crop failed (%s) — using full screenshot", e)
        return screenshot_b64


def get_chart_screenshot(pair: str = "") -> str | None:
    """Call the internal screenshot service. Returns base64 PNG or None.
    Retries up to API_RETRIES times on transient failures."""
    chart_url = get_chart_url(pair)
    params = {"url": chart_url} if chart_url else {}
    last_error = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            resp = requests.get(f"{SCREENSHOT_URL}/screenshot", params=params, timeout=60)
            if resp.status_code == 401:
                logger.error("TradingView session expired")
                telegram(
                    "DCA Bot: TradingView session expired\n"
                    "Update TV_COOKIES_JSON in your .env file and restart."
                )
                return None
            resp.raise_for_status()
            screenshot = resp.json().get("screenshot")
            return screenshot
        except Exception as e:
            last_error = e
            logger.warning("Screenshot attempt %d/%d failed: %s", attempt, API_RETRIES, e)
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
    logger.error("Screenshot service failed after %d attempts: %s", API_RETRIES, last_error)
    return None


def extract_multiplier(screenshot_b64: str) -> float:
    """Send chart screenshot to Claude Vision and extract the multiplier value.
    Retries up to API_RETRIES times on transient API failures."""
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — defaulting multiplier to 1.0")
        return 1.0

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    last_error = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_b64,
                            }
                        },
                        {
                            "type": "text",
                            "text": (
                                "This is a TradingView chart with the DCAS Calculator indicator. "
                                "Green triangle buy signals appear on the chart, each with a small blue text label directly below or beside it showing a multiplier like x2.5 or x4.02. "
                                "There may also be a white popup box on the right side showing 'DCA vs DCAS' stats — ignore that box completely. "
                                "Find the green triangle furthest to the RIGHT edge of the chart (the most recent signal). "
                                "Read the blue multiplier label (e.g. x4.02) next to that rightmost green triangle. "
                                "Return ONLY the number — no x, no text, just the digits (e.g. 4.02). "
                                "If you cannot find any label, return 1.0."
                            )
                        }
                    ]
                }]
            )
            raw = response.content[0].text.strip()
            match = re.search(r'\d+\.?\d*', raw)
            if not match:
                logger.warning("Vision returned no number: %r — defaulting to 1.0", raw)
                return 1.0
            multiplier = float(match.group())
            logger.info("Vision extracted multiplier: %s", multiplier)
            return multiplier

        except Exception as e:
            last_error = e
            logger.warning("Vision API attempt %d/%d failed: %s", attempt, API_RETRIES, e)
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)

    logger.error("Vision API failed after %d attempts: %s — defaulting to 1.0", API_RETRIES, last_error)
    return 1.0

# -- Trade execution ----------------------------------------------------------
def execute_buy(pair: str, multiplier: float = 1.0) -> bool:
    try:
        stake = round(BASE_STAKE * multiplier, 2)

        if stake < MIN_STAKE:
            msg = f"DCA BUY skipped for {pair} — stake {stake:.2f} below minimum {MIN_STAKE:.0f}"
            logger.warning(msg)
            telegram(msg)
            db.log_buy(pair, stake, multiplier, status="skipped")
            return False

        multiplier_str = f" (x{multiplier} multiplier)" if multiplier != 1.0 else ""
        logger.info("BUY %s: stake=%.2f%s", pair, stake, multiplier_str)

        result    = _ft_request("POST", "/forcebuy", json={"pair": pair, "stake_amount": stake})
        trade_id  = result.get("trade_id") or result.get("id", "?")
        open_rate = result.get("open_rate")

        db.log_buy(pair, stake, multiplier, trade_id=trade_id, open_rate=open_rate, status="executed")

        telegram(
            f"DCA BUY executed\n"
            f"Pair: {pair}\n"
            f"Stake: {stake:.2f}{multiplier_str}\n"
            f"Rate: {open_rate}\n"
            f"Trade ID: {trade_id}"
        )
        logger.info("BUY success: %s trade_id=%s stake=%.2f", pair, trade_id, stake)
        return True

    except Exception as e:
        logger.error("BUY failed for %s: %s", pair, e)
        db.log_buy(pair, stake if 'stake' in dir() else 0, multiplier, status="failed")
        telegram(f"DCA BUY FAILED: {pair} — check logs\n{e}")
        return False

# -- Auth decorator -----------------------------------------------------------
def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get("token") or request.headers.get("X-Token")
        if SECRET_TOKEN and token != SECRET_TOKEN:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# -- Endpoints ----------------------------------------------------------------

@app.route("/dca-buy", methods=["POST"])
@limiter.limit("10 per minute")
@require_token
def dca_buy():
    """
    DCA buy signal from DCAS Calculator.

    TradingView webhook body:
        {"pair": "BTC/AUD"}

    Auth: ?token=YOUR_SECRET_TOKEN or X-Token header.

    The bot automatically screenshots your TradingView chart and extracts
    the multiplier using Claude Vision. No multiplier needed in the payload.
    """
    data = request.get_json(silent=True) or {}
    pair = data.get("pair", TRADING_PAIR)

    if not valid_pair(pair):
        return jsonify({"error": "invalid pair"}), 400

    logger.info("DCA BUY signal received: %s", pair)

    # Get multiplier from chart via screenshot + vision
    multiplier = 1.0
    screenshot = get_chart_screenshot(pair)
    if screenshot:
        multiplier = extract_multiplier(screenshot)
        logger.info("Using multiplier: %s", multiplier)
    else:
        logger.warning("Screenshot unavailable — buying with multiplier 1.0")
        telegram(f"DCA BUY {pair}: screenshot failed, using base stake {BASE_STAKE}")

    success = execute_buy(pair, multiplier)
    return jsonify({
        "status":     "trade_executed" if success else "trade_failed",
        "multiplier": multiplier,
    }), 200


# -- Status / health ----------------------------------------------------------

@app.route("/status", methods=["GET"])
@limiter.limit("60 per minute")
@require_token
def status():
    pair = request.args.get("pair", TRADING_PAIR)
    try:
        trade = get_open_trade(pair)
        free  = get_free_balance()
        trade_info = None
        if trade:
            trade_info = {
                "trade_id":     trade["trade_id"],
                "amount":       trade["amount"],
                "open_rate":    trade["open_rate"],
                "current_rate": trade.get("current_rate"),
                "profit_pct":   round(trade.get("current_profit_pct", 0) * 100, 2),
                "open_date":    trade["open_date"],
            }
        return jsonify({
            "pair":         pair,
            "open_trade":   trade_info,
            "free_balance": round(free, 2),
            "base_stake":   BASE_STAKE,
            "status":       "ok",
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# -- Buy log API --------------------------------------------------------------

@app.route("/api/buys", methods=["GET"])
@limiter.limit("60 per minute")
@require_token
def api_buys():
    """JSON buy history for the dashboard."""
    return jsonify({
        "summary": db.get_summary(),
        "buys":    db.get_buys(limit=200),
    })


# -- Dashboard ----------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DCA Bot</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 24px; color: #f8fafc; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
           gap: 16px; margin-bottom: 32px; }
  .card { background: #1e2230; border-radius: 12px; padding: 20px; }
  .card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase;
                 letter-spacing: .05em; margin-bottom: 8px; }
  .card .value { font-size: 1.8rem; font-weight: 700; color: #f8fafc; }
  .card .value.green { color: #4ade80; }
  table { width: 100%; border-collapse: collapse; background: #1e2230; border-radius: 12px;
          overflow: hidden; }
  thead { background: #161926; }
  th { padding: 12px 16px; text-align: left; font-size: 0.75rem; color: #94a3b8;
       text-transform: uppercase; letter-spacing: .05em; }
  td { padding: 12px 16px; font-size: 0.875rem; border-top: 1px solid #2d3748; }
  tr:hover td { background: #252b3b; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 9999px;
           font-size: 0.7rem; font-weight: 600; text-transform: uppercase; }
  .badge.executed { background: #14532d; color: #4ade80; }
  .badge.failed   { background: #450a0a; color: #f87171; }
  .badge.skipped  { background: #1c1917; color: #a8a29e; }
  .refresh { font-size: 0.75rem; color: #64748b; margin-bottom: 12px; }
</style>
</head>
<body>
<h1>DCA Bot</h1>
<div class="cards" id="cards">
  <div class="card"><div class="label">Total Buys</div><div class="value" id="total_buys">—</div></div>
  <div class="card"><div class="label">Total Invested</div><div class="value green" id="total_invested">—</div></div>
  <div class="card"><div class="label">Avg Multiplier</div><div class="value" id="avg_multiplier">—</div></div>
  <div class="card"><div class="label">Max Multiplier</div><div class="value" id="max_multiplier">—</div></div>
</div>
<div class="refresh" id="refresh_time">Loading...</div>
<table>
  <thead>
    <tr>
      <th>Date (UTC)</th><th>Pair</th><th>Stake</th>
      <th>Multiplier</th><th>Rate</th><th>Trade ID</th><th>Status</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
<script>
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';
async function load() {
  const r = await fetch('/api/buys?token=' + TOKEN);
  if (r.status === 401) { document.body.innerHTML = '<p style="color:#f87171;padding:40px">Unauthorized — add ?token=YOUR_TOKEN to the URL</p>'; return; }
  const d = await r.json();
  const s = d.summary;
  document.getElementById('total_buys').textContent      = s.total_buys;
  document.getElementById('total_invested').textContent  = s.total_invested.toFixed(2);
  document.getElementById('avg_multiplier').textContent  = s.avg_multiplier.toFixed(2) + 'x';
  document.getElementById('max_multiplier').textContent  = s.max_multiplier.toFixed(2) + 'x';
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = d.buys.map(b => `
    <tr>
      <td>${b.timestamp}</td>
      <td><strong>${b.pair}</strong></td>
      <td>${b.stake.toFixed(2)}</td>
      <td>${b.multiplier.toFixed(2)}x</td>
      <td>${b.open_rate ? b.open_rate.toFixed(2) : '—'}</td>
      <td>${b.trade_id || '—'}</td>
      <td><span class="badge ${b.status}">${b.status}</span></td>
    </tr>`).join('');
  document.getElementById('refresh_time').textContent =
    'Last updated: ' + new Date().toLocaleTimeString() + ' — auto-refreshes every 60s';
}
load();
setInterval(load, 60000);
</script>
</body>
</html>"""


@app.route("/dashboard", methods=["GET"])
@limiter.limit("30 per minute")
@require_token
def dashboard():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
