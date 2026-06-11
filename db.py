"""SQLite buy log — persists all DCA buy records."""

import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "/data/dca_bot.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                pair        TEXT    NOT NULL,
                stake       REAL    NOT NULL,
                multiplier  REAL    NOT NULL DEFAULT 1.0,
                trade_id    TEXT,
                open_rate   REAL,
                status      TEXT    NOT NULL DEFAULT 'executed'
            )
        """)
        conn.commit()


def log_buy(pair: str, stake: float, multiplier: float,
            trade_id=None, open_rate=None, status: str = "executed"):
    """Insert a buy record."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO buys (timestamp, pair, stake, multiplier, trade_id, open_rate, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, pair, stake, multiplier, str(trade_id) if trade_id else None, open_rate, status)
        )
        conn.commit()


def get_buys(limit: int = 200) -> list[dict]:
    """Return most recent buys as a list of dicts."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM buys ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_summary() -> dict:
    """Return aggregate stats."""
    with _connect() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)            AS total_buys,
                COALESCE(SUM(stake), 0)  AS total_invested,
                COALESCE(AVG(multiplier), 0) AS avg_multiplier,
                COALESCE(MAX(multiplier), 0) AS max_multiplier
            FROM buys
            WHERE status = 'executed'
        """).fetchone()
    return dict(row)
