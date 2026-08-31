"""
Backtest: C1/C2/C3 pattern on 15m BTC/USDT candles.

Bearish setup  → C1 green, C2 takes C1 high AND closes red  → watch C3 red
Bullish setup  → C1 red,   C2 takes C1 low  AND closes green → watch C3 green

Run with real data: python pattern_backtest.py
Defaults to 180 days of 15m BTC/USDT from Binance (no API key needed).
"""

import urllib.request
import urllib.parse
import json
import time
import random
import math
from datetime import datetime, timedelta


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_klines_binance(symbol: str, interval: str, start_ms: int, limit: int = 1000):
    url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
        {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": limit}
    )
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def fetch_real_data(symbol="BTCUSDT", interval="15m", days=180) -> list[dict]:
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    candles = []
    current_ms = start_ms

    print(f"Fetching {days}d of {interval} {symbol} candles from Binance …")
    while True:
        batch = fetch_klines_binance(symbol, interval, current_ms)
        if not batch:
            break
        for row in batch:
            candles.append({
                "time":   datetime.utcfromtimestamp(row[0] / 1000).isoformat(),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })
        current_ms = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.08)

    print(f"  → {len(candles):,} candles loaded.\n")
    return candles


# ── Synthetic data (fallback) ──────────────────────────────────────────────────

def synthetic_btc_candles(n: int = 17_280, seed: int = 42) -> list[dict]:
    """
    Geometric Brownian Motion with realistic BTC 15m parameters.
    sigma ≈ 0.45% per 15m bar  (≈ 3% daily vol)
    mu    ≈ 0 (no drift for neutral test)
    """
    rng = random.Random(seed)
    price = 65_000.0
    sigma = 0.0045
    candles = []
    t = datetime(2024, 1, 1)

    for _ in range(n):
        ret = rng.gauss(0, sigma)
        open_p = price
        close_p = price * math.exp(ret)

        wick_up   = abs(rng.gauss(0, sigma * 0.6))
        wick_down = abs(rng.gauss(0, sigma * 0.6))
        high_p  = max(open_p, close_p) * (1 + wick_up)
        low_p   = min(open_p, close_p) * (1 - wick_down)

        candles.append({
            "time":   t.isoformat(),
            "open":   round(open_p, 2),
            "high":   round(high_p, 2),
            "low":    round(low_p, 2),
            "close":  round(close_p, 2),
            "volume": round(rng.uniform(10, 300), 4),
        })
        price = close_p
        t += timedelta(minutes=15)

    return candles


# ── Pattern detection ─────────────────────────────────────────────────────────

def scan_patterns(candles: list[dict]) -> dict:
    bear_total = bear_wins = 0
    bull_total = bull_wins = 0

    bear_examples: list[dict] = []
    bull_examples: list[dict] = []

    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]

        c1_green = c1["close"] > c1["open"]
        c1_red   = c1["close"] < c1["open"]
        c2_green = c2["close"] > c2["open"]
        c2_red   = c2["close"] < c2["open"]
        c3_green = c3["close"] > c3["open"]
        c3_red   = c3["close"] < c3["open"]

        # ── Bearish setup ──────────────────────────────────────────────────────
        # C1 green | C2 takes C1 high (high >= C1 high) and closes red
        if c1_green and c2["high"] >= c1["high"] and c2_red:
            bear_total += 1
            win = c3_red
            if win:
                bear_wins += 1
            if len(bear_examples) < 3:
                bear_examples.append({
                    "c1_time": c1["time"], "c1": c1, "c2": c2, "c3": c3, "win": win
                })

        # ── Bullish setup ──────────────────────────────────────────────────────
        # C1 red | C2 takes C1 low (low <= C1 low) and closes green
        if c1_red and c2["low"] <= c1["low"] and c2_green:
            bull_total += 1
            win = c3_green
            if win:
                bull_wins += 1
            if len(bull_examples) < 3:
                bull_examples.append({
                    "c1_time": c1["time"], "c1": c1, "c2": c2, "c3": c3, "win": win
                })

    return {
        "bearish": {"total": bear_total, "wins": bear_wins, "examples": bear_examples},
        "bullish": {"total": bull_total, "wins": bull_wins, "examples": bull_examples},
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def report(results: dict, data_label: str, total_candles: int):
    def pct(wins, total):
        return f"{wins / total * 100:.1f}%" if total else "N/A"

    bear = results["bearish"]
    bull = results["bullish"]

    print("=" * 60)
    print(f"  C1/C2/C3 Pattern Backtest — {data_label}")
    print(f"  {total_candles:,} candles  |  15m BTC/USDT")
    print("=" * 60)

    print(f"\n  BEARISH SETUP  (C1 green → C2 takes high + red → C3 red?)")
    print(f"  Occurrences : {bear['total']:,}")
    print(f"  C3 red wins : {bear['wins']:,}")
    print(f"  Win rate    : {pct(bear['wins'], bear['total'])}")

    print(f"\n  BULLISH SETUP  (C1 red → C2 takes low + green → C3 green?)")
    print(f"  Occurrences : {bull['total']:,}")
    print(f"  C3 green wins : {bull['wins']:,}")
    print(f"  Win rate    : {pct(bull['wins'], bull['total'])}")

    combined_total = bear["total"] + bull["total"]
    combined_wins  = bear["wins"]  + bull["wins"]
    print(f"\n  COMBINED")
    print(f"  Occurrences : {combined_total:,}")
    print(f"  Win rate    : {pct(combined_wins, combined_total)}")
    print()

    # Sample bearish examples
    if bear["examples"]:
        print("  Sample bearish setups:")
        for ex in bear["examples"]:
            c1, c2, c3 = ex["c1"], ex["c2"], ex["c3"]
            result_str = "WIN ✓" if ex["win"] else "LOSS ✗"
            print(f"    [{ex['c1_time']}]  "
                  f"C1 O={c1['open']:.0f} C={c1['close']:.0f}  "
                  f"C2 H={c2['high']:.0f} C={c2['close']:.0f}  "
                  f"C3 C={c3['close']:.0f}  → {result_str}")

    if bull["examples"]:
        print("  Sample bullish setups:")
        for ex in bull["examples"]:
            c1, c2, c3 = ex["c1"], ex["c2"], ex["c3"]
            result_str = "WIN ✓" if ex["win"] else "LOSS ✗"
            print(f"    [{ex['c1_time']}]  "
                  f"C1 O={c1['open']:.0f} C={c1['close']:.0f}  "
                  f"C2 L={c2['low']:.0f} C={c2['close']:.0f}  "
                  f"C3 C={c3['close']:.0f}  → {result_str}")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main(use_real_data: bool = True, days: int = 180):
    if use_real_data:
        try:
            candles = fetch_real_data(days=days)
            label = f"Real Binance data ({days}d)"
        except Exception as e:
            print(f"[!] Could not reach Binance ({e}). Falling back to synthetic data.\n")
            use_real_data = False

    if not use_real_data:
        bars = days * 24 * 4  # 15m bars per day
        candles = synthetic_btc_candles(n=bars)
        label = f"Synthetic GBM data ({days}d equivalent)"

    results = scan_patterns(candles)
    report(results, label, len(candles))


if __name__ == "__main__":
    main(use_real_data=True, days=180)
