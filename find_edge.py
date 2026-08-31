"""
Multi-timeframe C1/C2/C3 edge finder — 30m, 1h, 2h, 4h, 8h, 12h, 1d

Bearish: C1 green → C2 takes C1 high + closes red  → enter short at C3 open
Bullish: C1 red   → C2 takes C1 low  + closes green → enter long  at C3 open

Stop  = C2 extreme (C2 high for bear, C2 low for bull)
Target = 1R / 1.5R / 2R / 3R from entry

Sort by: Expected Value (avg R per trade)  — the real money metric.

Run locally:  python find_edge.py --real
"""

import urllib.request, urllib.parse, json, time
import random, math, sys, itertools
from datetime import datetime, timedelta
from dataclasses import dataclass


# ── CLI ────────────────────────────────────────────────────────────────────────
args       = sys.argv[1:]
use_real   = "--real" in args
min_trades = 30
top_n      = 12
for i, a in enumerate(args):
    if a == "--min-trades" and i+1 < len(args): min_trades = int(args[i+1])
    if a == "--top"        and i+1 < len(args): top_n      = int(args[i+1])


# ── Timeframe config ───────────────────────────────────────────────────────────
# sigma = vol per bar using BTC ~85% annual vol
# sqrt(minutes / 525600) * 0.85
def _sigma(minutes): return math.sqrt(minutes / 525_600) * 0.85

TF = {
    "30m": {"bpd": 48,  "sigma": _sigma(30),   "sim_days": 600,  "real_days": 500,  "binance": "30m"},
    "1h":  {"bpd": 24,  "sigma": _sigma(60),   "sim_days": 1200, "real_days": 730,  "binance": "1h"},
    "2h":  {"bpd": 12,  "sigma": _sigma(120),  "sim_days": 2000, "real_days": 1000, "binance": "2h"},
    "4h":  {"bpd": 6,   "sigma": _sigma(240),  "sim_days": 3000, "real_days": 1000, "binance": "4h"},
    "8h":  {"bpd": 3,   "sigma": _sigma(480),  "sim_days": 4000, "real_days": 1000, "binance": "8h"},
    "12h": {"bpd": 2,   "sigma": _sigma(720),  "sim_days": 5000, "real_days": 1000, "binance": "12h"},
    "1d":  {"bpd": 1,   "sigma": _sigma(1440), "sim_days": 8000, "real_days": 1000, "binance": "1d"},
}


# ── Data ───────────────────────────────────────────────────────────────────────

def fetch_binance(symbol, interval, days):
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    candles, cur = [], start_ms
    print(f"  Fetching {days}d {interval} …", end=" ", flush=True)
    while True:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
        )
        with urllib.request.urlopen(url, timeout=15) as r:
            batch = json.loads(r.read())
        if not batch: break
        for row in batch:
            candles.append({"open": float(row[1]), "high": float(row[2]),
                            "low":  float(row[3]), "close": float(row[4])})
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.08)
    print(f"{len(candles):,} bars")
    return candles


def synthetic(n, sigma, seed=42):
    rng, price = random.Random(seed), 65_000.0
    out = []
    for _ in range(n):
        ret = rng.gauss(0, sigma)
        o, c = price, price * math.exp(ret)
        wu = abs(rng.gauss(0, sigma * 0.55))
        wd = abs(rng.gauss(0, sigma * 0.55))
        h = max(o, c) * (1 + wu)
        l = min(o, c) * (1 - wd)
        out.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    return out


# ── Trade simulation ───────────────────────────────────────────────────────────

def sim_trade(c2, c3, direction, r_mult):
    """
    Returns R gained (positive = profit, negative = loss).
    None = invalid setup (entry past stop).
    """
    entry = c3["open"]
    stop  = c2["high"] if direction == "bear" else c2["low"]
    risk  = (stop - entry) if direction == "bear" else (entry - stop)
    if risk <= 0:
        return None                    # entry already past stop — skip

    target = entry - risk * r_mult if direction == "bear" else entry + risk * r_mult

    if direction == "bear":
        stop_hit   = c3["high"] >= stop
        target_hit = c3["low"]  <= target
    else:
        stop_hit   = c3["low"]  <= stop
        target_hit = c3["high"] >= target

    if stop_hit and not target_hit:    return -1.0
    if target_hit and not stop_hit:    return r_mult
    if target_hit and stop_hit:        return -1.0    # conservative: stop first
    # Neither hit — exit at C3 close
    pnl = (entry - c3["close"]) if direction == "bear" else (c3["close"] - entry)
    return pnl / risk


# ── Filter dataclass ───────────────────────────────────────────────────────────

@dataclass
class F:
    min_c1_body_ratio: float = 0.0
    min_c2_ext_pct:    float = 0.0
    c2_close_strong:   bool  = False
    c2_engulfs_c1:     bool  = False
    r_mult:            float = 1.0


def label(f):
    parts = []
    if f.min_c1_body_ratio: parts.append(f"c1qual≥{f.min_c1_body_ratio}")
    if f.min_c2_ext_pct:    parts.append(f"c2ext≥{f.min_c2_ext_pct}%")
    if f.c2_close_strong:   parts.append("c2strong")
    if f.c2_engulfs_c1:     parts.append("engulf")
    return (", ".join(parts) or "no filters") + f"  TP={f.r_mult}R"


# ── Backtest ───────────────────────────────────────────────────────────────────

def backtest(candles, f):
    trades = []

    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i+1], candles[i+2]

        body  = abs(c1["close"] - c1["open"])
        rng   = c1["high"] - c1["low"] or 1e-9
        rng2  = c2["high"] - c2["low"] or 1e-9

        if f.min_c1_body_ratio and body / rng < f.min_c1_body_ratio:
            continue

        c1g = c1["close"] > c1["open"]
        c2r = c2["close"] < c2["open"]
        c2g = c2["close"] > c2["open"]

        # ── Bearish ────────────────────────────────────────────────────────────
        if c1g and c2["high"] >= c1["high"] and c2r:
            ext = (c2["high"] - c1["high"]) / c1["close"] * 100
            sc  = (c2["close"] - c2["low"]) / rng2 <= 0.40
            eng = c2["close"] <= c1["open"]
            ok  = (not f.min_c2_ext_pct  or ext >= f.min_c2_ext_pct) and \
                  (not f.c2_close_strong  or sc) and \
                  (not f.c2_engulfs_c1    or eng)
            if ok:
                r = sim_trade(c2, c3, "bear", f.r_mult)
                if r is not None:
                    trades.append(r)

        # ── Bullish ────────────────────────────────────────────────────────────
        if not c1g and c2["low"] <= c1["low"] and c2g:
            ext = (c1["low"] - c2["low"]) / c1["close"] * 100
            sc  = (c2["high"] - c2["close"]) / rng2 <= 0.40
            eng = c2["close"] >= c1["open"]
            ok  = (not f.min_c2_ext_pct  or ext >= f.min_c2_ext_pct) and \
                  (not f.c2_close_strong  or sc) and \
                  (not f.c2_engulfs_c1    or eng)
            if ok:
                r = sim_trade(c2, c3, "bull", f.r_mult)
                if r is not None:
                    trades.append(r)

    if len(trades) < min_trades:
        return None

    wins   = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    total  = len(trades)
    wr     = len(wins) / total * 100
    ev     = sum(trades) / total
    pf     = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    total_r = sum(trades)

    return {
        "trades": total, "wr": wr, "ev": ev,
        "pf": pf, "total_r": total_r, "f": f,
    }


# ── Sweep ──────────────────────────────────────────────────────────────────────

GRID = {
    "min_c1_body_ratio": [0.0, 0.40, 0.55, 0.70],
    "min_c2_ext_pct":    [0.0, 0.05, 0.15, 0.25],
    "c2_close_strong":   [False, True],
    "c2_engulfs_c1":     [False, True],
    "r_mult":            [1.0, 1.5, 2.0, 3.0],
}

def sweep(candles):
    keys   = list(GRID.keys())
    combos = list(itertools.product(*GRID.values()))
    results = []
    for combo in combos:
        f = F(**dict(zip(keys, combo)))
        r = backtest(candles, f)
        if r:
            results.append(r)
    return results


# ── Print ──────────────────────────────────────────────────────────────────────

def print_tf(tf, rows, n_bars, src):
    if not rows:
        print(f"\n  [{tf}] No combos met {min_trades} trades.")
        return

    by_ev    = sorted(rows, key=lambda r: r["ev"],    reverse=True)[:top_n]
    by_trade = sorted([r for r in rows if r["ev"] > 0], key=lambda r: r["trades"], reverse=True)[:top_n]
    baseline = next((r for r in rows
                     if not any([r["f"].min_c1_body_ratio, r["f"].min_c2_ext_pct,
                                 r["f"].c2_close_strong, r["f"].c2_engulfs_c1])
                     and r["f"].r_mult == 1.0), None)

    print(f"\n{'='*76}")
    print(f"  {tf.upper()} — {src}  |  {n_bars:,} bars")
    if baseline:
        print(f"  Baseline (no filters, 1R): WR={baseline['wr']:.1f}%  "
              f"EV={baseline['ev']:+.3f}R  Trades={baseline['trades']:,}")
    print(f"  EV>0 combos: {len([r for r in rows if r['ev']>0])}")
    print(f"{'='*76}")

    hdr = f"  {'#':>3}  {'EV/trade':>9}  {'WR%':>6}  {'PF':>5}  {'TotalR':>8}  {'Trades':>7}  Config"
    print(f"\n  ── TOP BY EXPECTED VALUE (avg R per trade) ──")
    print(hdr); print("  " + "-"*74)
    for i, r in enumerate(by_ev, 1):
        print(f"  {i:>3}  {r['ev']:>+9.4f}  {r['wr']:>6.1f}  {r['pf']:>5.2f}  "
              f"{r['total_r']:>+8.1f}  {r['trades']:>7,}  {label(r['f'])}")

    if by_trade:
        print(f"\n  ── MOST TRADES WITH POSITIVE EV ──")
        print(hdr); print("  " + "-"*74)
        for i, r in enumerate(by_trade, 1):
            print(f"  {i:>3}  {r['ev']:>+9.4f}  {r['wr']:>6.1f}  {r['pf']:>5.2f}  "
                  f"{r['total_r']:>+8.1f}  {r['trades']:>7,}  {label(r['f'])}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    mode = "Real Binance" if use_real else "Synthetic GBM"
    combos = len(list(itertools.product(*GRID.values())))
    print(f"C1/C2/C3 Full Edge Search  |  {mode}  |  {combos} combos × {len(TF)} timeframes\n")

    for tf, cfg in TF.items():
        candles = None
        if use_real:
            try:
                candles = fetch_binance("BTCUSDT", cfg["binance"], cfg["real_days"])
                src = f"Binance {cfg['real_days']}d"
            except Exception as e:
                print(f"  [{tf}] Binance unavailable — using synthetic.")

        if candles is None:
            n = cfg["sim_days"] * cfg["bpd"]
            candles = synthetic(n, cfg["sigma"])
            src = f"Synthetic {cfg['sim_days']}d"

        print(f"  [{tf}] sweeping {combos} combos …", end=" ", flush=True)
        rows = sweep(candles)
        print(f"done  ({len(rows)} valid)")
        print_tf(tf, rows, len(candles), src)

    print()


if __name__ == "__main__":
    main()
