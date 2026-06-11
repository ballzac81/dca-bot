#!/usr/bin/env python3
"""Screenshot service — internal Playwright container.

Keeps TradingView session alive via cookies (no login required, 2FA preserved).
Exposes a single internal endpoint used only by dca-bot.
NOT exposed outside Docker network.
"""

import base64
import json
import logging
import os
import time

from flask import Flask, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

TV_CHART_URL    = os.environ.get("TV_CHART_URL", "")       # Full URL to your TradingView chart
TV_COOKIES_JSON = os.environ.get("TV_COOKIES_JSON", "[]")  # JSON cookie array from Cookie-Editor
CHART_WAIT_MS   = int(os.environ.get("CHART_WAIT_MS", "5000"))  # ms to wait for chart to render


@app.route("/screenshot", methods=["GET"])
def screenshot():
    """Take a screenshot of the TradingView chart and return it as base64 PNG."""
    if not TV_CHART_URL:
        return jsonify({"error": "TV_CHART_URL not configured"}), 500

    try:
        cookies = json.loads(TV_COOKIES_JSON)
    except json.JSONDecodeError:
        return jsonify({"error": "TV_COOKIES_JSON is not valid JSON"}), 500

    # Playwright only accepts sameSite: Strict | Lax | None
    # Browser-exported cookies often have other values (e.g. "unspecified", "no_restriction")
    _VALID_SAMESITE = {"Strict", "Lax", "None"}
    _SAMESITE_MAP = {
        "no_restriction": "None",
        "lax":            "Lax",
        "strict":         "Strict",
        "unspecified":    "Lax",
    }
    for c in cookies:
        ss = c.get("sameSite", "")
        if ss not in _VALID_SAMESITE:
            c["sameSite"] = _SAMESITE_MAP.get(ss.lower(), "Lax")

    logger.info("Taking TradingView screenshot: %s", TV_CHART_URL)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )

            # Inject saved session cookies — no login, 2FA untouched
            if cookies:
                context.add_cookies(cookies)

            page = context.new_page()

            try:
                page.goto(TV_CHART_URL, wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                # networkidle can time out on heavy charts; domcontentloaded is enough
                logger.warning("networkidle timed out, proceeding with domcontentloaded")
                page.goto(TV_CHART_URL, wait_until="domcontentloaded", timeout=30000)

            # Wait for chart candles and indicator labels to render
            page.wait_for_timeout(CHART_WAIT_MS)

            # Check we're not on a login page (session expired)
            if "signin" in page.url or "login" in page.url:
                browser.close()
                logger.error("TradingView session expired — cookies need refreshing")
                return jsonify({"error": "session_expired"}), 401

            img_bytes = page.screenshot(full_page=False)
            browser.close()

        img_b64 = base64.b64encode(img_bytes).decode()
        logger.info("Screenshot captured (%d bytes)", len(img_bytes))
        return jsonify({"screenshot": img_b64})

    except Exception as e:
        logger.error("Screenshot failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
