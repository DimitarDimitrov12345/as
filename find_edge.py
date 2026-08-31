"""
Parameter sweep to find the best edge on the C1/C2/C3 pattern.

Bearish: C1 green → C2 takes C1 high + closes red → C3 red
Bullish: C1 red  → C2 takes C1 low  + closes green → C3 green

Filters swept:
  min_c1_body_pct    — C1 body / close must be >= X% (removes doji C1s)
  min_c1_body_ratio  — C1 body / C1 range must be >= X  (C1 quality)
  min_c2_ext_pct     — C2 must exceed C1 high/low by >= X% (real wick)
  c2_close_strong    — C2 must close in the weakest 40% of its own range
  c2_engulfs_c1      — C2 close must cross C1 open (full engulf)

Usage:  python find_edge.py
        python find_edge.py --days 365    # more history
        python find_edge.py --min-trades 30
"""

import urllib.request, urllib.parse, json, time, random, math, sys, itertools
from datetime import datetime, timedelta
from dataclasses import dataclass


# ── CLI args (simple) ──────────────────────────────────────────────────────────

days_back   = 180
min_trades  = 30
top_n       = 20

for i, arg in enumerate(sys.argv[1:]):
    if arg == "--days"        and i + 1 < len(sys.argv) - 1: days_back  = int(sys.argv[i + 2])
    if arg == "--min-trades"  and i + 1 < len(sys.argv) - 1: min_trades = int(sys.argv[i + 2])
    if arg == "--top"         and i + 1 < len(sys.argv) - 1: top_n      = int(sys.argv[i + 2])


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_real_data(symbol="BTCUSDT", interval="15m", days=180):
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    candles, current_ms = [], start_ms
    print(f"Fetching {days}d of {interval} {symbol} …", flush=True)
    while True:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": current_ms, "limit": 1000}
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
        current_ms = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.08)
    print(f"  → {len(candles):,} candles.\n")
    return candles


def synthetic_candles(n=17_280, seed=42):
    rng, price, sigma = random.Random(seed), 65_000.0, 0.0045
    out = []
    for _ in range(n):
        ret    = rng.gauss(0, sigma)
        o, c   = price, price * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma * 0.6)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma * 0.6)))
        out.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    return out


# ── Single-filter backtest ─────────────────────────────────────────────────────

@dataclass
class FilterSet:
    min_c1_body_pct:   float = 0.0   # e.g. 0.10 = body >= 0.10% of price
    min_c1_body_ratio: float = 0.0   # body / (high-low); 0=any, 0.4=decent candle
    min_c2_ext_pct:    float = 0.0   # C2 must exceed C1 H/L by >= X%
    c2_close_strong:   bool  = False  # C2 closes in worst 40% of its range
    c2_engulfs_c1:     bool  = False  # C2 close crosses C1 open


def run(candles: list, f: FilterSet):
    bear_t = bear_w = bull_t = bull_w = 0

    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i+1], candles[i+2]

        c1_body  = abs(c1["close"] - c1["open"])
        c1_range = c1["high"] - c1["low"] or 1e-9
        c2_range = c2["high"] - c2["low"] or 1e-9

        # shared guards
        if f.min_c1_body_pct   and c1_body / c1["close"] * 100 < f.min_c1_body_pct:
            continue
        if f.min_c1_body_ratio and c1_body / c1_range < f.min_c1_body_ratio:
            continue

        c1g = c1["close"] > c1["open"]
        c1r = c1["close"] < c1["open"]
        c2g = c2["close"] > c2["open"]
        c2r = c2["close"] < c2["open"]
        c3g = c3["close"] > c3["open"]
        c3r = c3["close"] < c3["open"]

        # ── Bearish ────────────────────────────────────────────────────────────
        if c1g and c2["high"] >= c1["high"] and c2r:
            ext = (c2["high"] - c1["high"]) / c1["close"] * 100
            if f.min_c2_ext_pct and ext < f.min_c2_ext_pct:
                pass
            elif f.c2_close_strong and (c2["close"] - c2["low"]) / c2_range > 0.40:
                pass  # C2 didn't close near its low (not a strong red bar)
            elif f.c2_engulfs_c1 and c2["close"] > c1["open"]:
                pass  # didn't actually engulf
            else:
                bear_t += 1
                bear_w += c3r

        # ── Bullish ────────────────────────────────────────────────────────────
        if c1r and c2["low"] <= c1["low"] and c2g:
            ext = (c1["low"] - c2["low"]) / c1["close"] * 100
            if f.min_c2_ext_pct and ext < f.min_c2_ext_pct:
                pass
            elif f.c2_close_strong and (c2["high"] - c2["close"]) / c2_range > 0.40:
                pass  # C2 didn't close near its high
            elif f.c2_engulfs_c1 and c2["close"] < c1["open"]:
                pass
            else:
                bull_t += 1
                bull_w += c3g

    return bear_t, bear_w, bull_t, bull_w


# ── Sweep ──────────────────────────────────────────────────────────────────────

GRID = {
    "min_c1_body_pct":   [0.0, 0.10, 0.20, 0.30],
    "min_c1_body_ratio": [0.0, 0.35, 0.50, 0.65],
    "min_c2_ext_pct":    [0.0, 0.05, 0.10, 0.20],
    "c2_close_strong":   [False, True],
    "c2_engulfs_c1":     [False, True],
}


def sweep(candles):
    keys   = list(GRID.keys())
    values = list(GRID.values())
    results = []

    combos = list(itertools.product(*values))
    print(f"Testing {len(combos):,} filter combinations …", flush=True)

    for combo in combos:
        f = FilterSet(**dict(zip(keys, combo)))
        bt, bw, ut, uw = run(candles, f)

        total = bt + ut
        if total < min_trades:
            continue

        wins = bw + uw
        wr   = wins / total * 100
        edge = wr - 50.0

        bear_wr = bw / bt * 100 if bt else 0
        bull_wr = uw / ut * 100 if ut else 0

        results.append({
            "filter": f,
            "bear_trades": bt, "bear_wins": bw, "bear_wr": bear_wr,
            "bull_trades": ut, "bull_wins": uw, "bull_wr": bull_wr,
            "total": total, "wins": wins, "wr": wr, "edge": edge,
        })

    results.sort(key=lambda x: x["wr"], reverse=True)
    return results


# ── Print results ──────────────────────────────────────────────────────────────

def print_results(results, label, total_candles):
    print("=" * 72)
    print(f"  EDGE FINDER — {label}")
    print(f"  {total_candles:,} candles  |  min {min_trades} trades per combo  |  top {top_n} shown")
    print("=" * 72)

    if not results:
        print("  No combos met the minimum trade count.\n")
        return

    hdr = (f"  {'#':>3}  {'WR%':>6}  {'Edge':>6}  {'Trades':>7}  "
           f"{'B-WR%':>6}  {'BT':>5}  {'U-WR%':>6}  {'UT':>5}  Filters")
    print(hdr)
    print("  " + "-" * 68)

    for rank, r in enumerate(results[:top_n], 1):
        f = r["filter"]
        filters_str = []
        if f.min_c1_body_pct:   filters_str.append(f"c1body≥{f.min_c1_body_pct}%")
        if f.min_c1_body_ratio: filters_str.append(f"c1qual≥{f.min_c1_body_ratio}")
        if f.min_c2_ext_pct:    filters_str.append(f"c2ext≥{f.min_c2_ext_pct}%")
        if f.c2_close_strong:   filters_str.append("c2strong")
        if f.c2_engulfs_c1:     filters_str.append("engulf")
        if not filters_str:     filters_str = ["no filters (baseline)"]

        print(f"  {rank:>3}  {r['wr']:>6.1f}  {r['edge']:>+6.1f}  {r['total']:>7,}  "
              f"{r['bear_wr']:>6.1f}  {r['bear_trades']:>5,}  "
              f"{r['bull_wr']:>6.1f}  {r['bull_trades']:>5,}  "
              + ", ".join(filters_str))

    # Baseline (no filters)
    baseline = next((r for r in results if not any([
        r["filter"].min_c1_body_pct, r["filter"].min_c1_body_ratio,
        r["filter"].min_c2_ext_pct, r["filter"].c2_close_strong,
        r["filter"].c2_engulfs_c1,
    ])), None)
    if baseline:
        print(f"\n  Baseline (no filters): {baseline['wr']:.1f}% WR  |  {baseline['total']:,} trades")

    best = results[0]
    print(f"\n  Best combo: {best['wr']:.1f}% WR over {best['total']:,} trades")
    bf = best["filter"]
    print(f"    min_c1_body_pct   = {bf.min_c1_body_pct}")
    print(f"    min_c1_body_ratio = {bf.min_c1_body_ratio}")
    print(f"    min_c2_ext_pct    = {bf.min_c2_ext_pct}")
    print(f"    c2_close_strong   = {bf.c2_close_strong}")
    print(f"    c2_engulfs_c1     = {bf.c2_engulfs_c1}")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    try:
        candles = fetch_real_data(days=days_back)
        label   = f"Real Binance data ({days_back}d)"
    except Exception as e:
        print(f"[!] Binance unreachable ({e}). Using synthetic data.\n")
        candles = synthetic_candles(n=days_back * 24 * 4)
        label   = f"Synthetic GBM ({days_back}d equivalent)"

    results = sweep(candles)
    print_results(results, label, len(candles))


if __name__ == "__main__":
    main()
