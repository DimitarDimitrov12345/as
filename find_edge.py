"""
Multi-timeframe C1/C2/C3 pattern edge finder.
Tests 15m, 1h, and 4h candles.

Bearish: C1 green → C2 takes C1 high + closes red  → C3 red
Bullish: C1 red   → C2 takes C1 low  + closes green → C3 green

Usage:
  python find_edge.py                  # synthetic data (Binance blocked)
  python find_edge.py --real           # pull real Binance data
  python find_edge.py --min-trades 100
"""

import urllib.request, urllib.parse, json, time
import random, math, sys, itertools
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict


# ── CLI ────────────────────────────────────────────────────────────────────────

args         = sys.argv[1:]
use_real     = "--real" in args
min_trades   = 50
top_n        = 15

for i, a in enumerate(args):
    if a == "--min-trades" and i + 1 < len(args): min_trades = int(args[i + 1])
    if a == "--top"        and i + 1 < len(args): top_n      = int(args[i + 1])


# ── Binance fetch ──────────────────────────────────────────────────────────────

def fetch_binance(symbol, interval, days):
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    candles, cur = [], start_ms
    print(f"  Fetching {days}d {interval} {symbol} …", end=" ", flush=True)
    while True:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
        )
        with urllib.request.urlopen(url, timeout=15) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        for row in batch:
            candles.append({
                "open": float(row[1]), "high": float(row[2]),
                "low":  float(row[3]), "close": float(row[4]),
            })
        cur = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.08)
    print(f"{len(candles):,} bars")
    return candles


# ── Synthetic data ─────────────────────────────────────────────────────────────

def synthetic(n, seed=42, sigma=0.0045):
    rng, price = random.Random(seed), 65_000.0
    out = []
    for _ in range(n):
        ret = rng.gauss(0, sigma)
        o, c = price, price * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma * 0.55)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma * 0.55)))
        out.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    return out


# timeframe config: bars_per_day, sigma_per_bar, synthetic history (days)
TF_CONFIG = {
    "15m": {"bpd": 96,  "sigma": 0.0045, "sim_days": 500,  "real_days": 365},
    "1h":  {"bpd": 24,  "sigma": 0.0090, "sim_days": 1500, "real_days": 730},
    "4h":  {"bpd": 6,   "sigma": 0.0180, "sim_days": 3000, "real_days": 1000},
}


# ── Pattern + filters ──────────────────────────────────────────────────────────

@dataclass
class F:
    min_c1_body_ratio: float = 0.0   # C1 body/(high-low); 0=any
    min_c2_ext_pct:    float = 0.0   # C2 must exceed C1 H/L by >= X%
    c2_close_strong:   bool  = False  # C2 closes in bottom/top 40% of its range
    c2_engulfs_c1:     bool  = False  # C2 close crosses C1 open


def backtest(candles, f: F):
    bt = bw = ut = uw = 0
    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i+1], candles[i+2]

        c1_body  = abs(c1["close"] - c1["open"])
        c1_range = c1["high"] - c1["low"] or 1e-9
        c2_range = c2["high"] - c2["low"] or 1e-9

        if f.min_c1_body_ratio and c1_body / c1_range < f.min_c1_body_ratio:
            continue

        c1g = c1["close"] > c1["open"];  c1r = not c1g
        c2g = c2["close"] > c2["open"];  c2r = not c2g
        c3g = c3["close"] > c3["open"];  c3r = not c3g

        # bearish
        if c1g and c2["high"] >= c1["high"] and c2r:
            ext = (c2["high"] - c1["high"]) / c1["close"] * 100
            strong_close = (c2["close"] - c2["low"]) / c2_range <= 0.40
            engulfs = c2["close"] <= c1["open"]
            if (not f.min_c2_ext_pct    or ext >= f.min_c2_ext_pct) and \
               (not f.c2_close_strong   or strong_close) and \
               (not f.c2_engulfs_c1     or engulfs):
                bt += 1; bw += c3r

        # bullish
        if c1r and c2["low"] <= c1["low"] and c2g:
            ext = (c1["low"] - c2["low"]) / c1["close"] * 100
            strong_close = (c2["high"] - c2["close"]) / c2_range <= 0.40
            engulfs = c2["close"] >= c1["open"]
            if (not f.min_c2_ext_pct    or ext >= f.min_c2_ext_pct) and \
               (not f.c2_close_strong   or strong_close) and \
               (not f.c2_engulfs_c1     or engulfs):
                ut += 1; uw += c3g

    return bt, bw, ut, uw


# ── Grid sweep ─────────────────────────────────────────────────────────────────

GRID = {
    "min_c1_body_ratio": [0.0, 0.30, 0.50, 0.65],
    "min_c2_ext_pct":    [0.0, 0.05, 0.10, 0.20],
    "c2_close_strong":   [False, True],
    "c2_engulfs_c1":     [False, True],
}


def sweep(candles, label):
    keys   = list(GRID.keys())
    combos = list(itertools.product(*GRID.values()))
    rows   = []

    for combo in combos:
        f = F(**dict(zip(keys, combo)))
        bt, bw, ut, uw = backtest(candles, f)
        total = bt + ut
        if total < min_trades:
            continue
        wins = bw + uw
        wr   = wins / total * 100
        rows.append({
            "f": f, "total": total, "wins": wins, "wr": wr,
            "bt": bt, "bw": bw, "bwr": bw/bt*100 if bt else 0,
            "ut": ut, "uw": uw, "uwr": uw/ut*100 if ut else 0,
        })

    return rows


# ── Print ──────────────────────────────────────────────────────────────────────

def filter_label(f: F):
    parts = []
    if f.min_c1_body_ratio: parts.append(f"c1qual≥{f.min_c1_body_ratio}")
    if f.min_c2_ext_pct:    parts.append(f"c2ext≥{f.min_c2_ext_pct}%")
    if f.c2_close_strong:   parts.append("c2strong")
    if f.c2_engulfs_c1:     parts.append("engulf")
    return ", ".join(parts) if parts else "baseline (no filters)"


def print_table(rows, title, sort_key, top=15):
    sorted_rows = sorted(rows, key=lambda r: r[sort_key], reverse=True)[:top]
    print(f"\n  ── {title} ──")
    print(f"  {'#':>3}  {'WR%':>6}  {'Trades':>7}  {'Bear WR':>8}  {'Bull WR':>8}  Filters")
    print("  " + "-" * 70)
    for i, r in enumerate(sorted_rows, 1):
        print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  "
              f"{r['bwr']:>7.1f}%  {r['uwr']:>7.1f}%  {filter_label(r['f'])}")


def print_tf_results(tf, rows, n_candles, label):
    if not rows:
        print(f"\n  [{tf}] No combos met the minimum {min_trades} trades.")
        return

    over50 = [r for r in rows if r["wr"] > 50.0]
    baseline = next((r for r in rows if filter_label(r["f"]) == "baseline (no filters)"), None)

    print(f"\n{'='*72}")
    print(f"  {tf.upper()} — {label}  |  {n_candles:,} bars  |  min {min_trades} trades")
    if baseline:
        print(f"  Baseline : {baseline['wr']:.1f}% WR  |  {baseline['total']:,} trades")
    print(f"  Combos >50% WR : {len(over50)}")
    print(f"{'='*72}")

    # View 1: highest WR
    print_table(rows, "HIGHEST WIN RATE", "wr", top_n)

    # View 2: most trades with WR > 50%
    if over50:
        print_table(over50, "MOST TRADES  (WR > 50%)", "total", top_n)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    data_label = "Synthetic GBM"
    if use_real:
        data_label = "Real Binance"

    print(f"C1/C2/C3 Pattern Edge Finder  |  {data_label}  |  min {min_trades} trades\n")

    for tf, cfg in TF_CONFIG.items():
        candles = None
        if use_real:
            try:
                candles = fetch_binance("BTCUSDT", tf, cfg["real_days"])
                lbl = f"Binance {cfg['real_days']}d"
            except Exception as e:
                print(f"  [{tf}] Binance unavailable ({e}), using synthetic.")

        if candles is None:
            n = cfg["sim_days"] * cfg["bpd"]
            candles = synthetic(n, seed=42, sigma=cfg["sigma"])
            lbl = f"Synthetic {cfg['sim_days']}d equiv"

        rows = sweep(candles, tf)
        print_tf_results(tf, rows, len(candles), lbl)

    print()


if __name__ == "__main__":
    main()
