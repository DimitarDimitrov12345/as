"""
Multi-Timeframe C1/C2/C3 system for Polymarket candle direction betting.

Step 1 — Signal TF (1h or 4h):  C1/C2/C3 pattern fires → directional bias
Step 2 — Confirm TF (5m or 15m): check first N bars of C3 confirm direction
Step 3 — Bet on Polymarket that C3 closes in the predicted direction

Metric = Win Rate (Polymarket pays ~$0.50 on $1 bet; need WR > ~53% to profit)

Usage:
  python mtf_edge.py           # synthetic data
  python mtf_edge.py --real    # pull real Binance data
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from itertools import product as iproduct


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
MIN_OCC  = 30       # minimum pattern occurrences to report


# ── Binance fetch ──────────────────────────────────────────────────────────────
def fetch_binance(symbol, interval, days):
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    out, cur = [], start_ms
    print(f"  Fetching {days}d {interval} …", end=" ", flush=True)
    while True:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
        )
        with urllib.request.urlopen(url, timeout=15) as r:
            batch = json.loads(r.read())
        if not batch: break
        for row in batch:
            out.append({"open": float(row[1]), "high": float(row[2]),
                        "low":  float(row[3]), "close": float(row[4]),
                        "ts":   int(row[0])})
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.08)
    print(f"{len(out):,} bars")
    return out


# ── Synthetic: generate fine-grain then aggregate ─────────────────────────────
def _sigma(minutes): return math.sqrt(minutes / 525_600) * 0.85

def gen_fine(minutes_per_bar, total_bars, seed=42):
    sig = _sigma(minutes_per_bar)
    rng, price = random.Random(seed), 65_000.0
    out, ts = [], 0
    for _ in range(total_bars):
        ret = rng.gauss(0, sig)
        o, c = price, price * math.exp(ret)
        wu = abs(rng.gauss(0, sig * 0.55))
        wd = abs(rng.gauss(0, sig * 0.55))
        h = max(o, c) * (1 + wu)
        l = min(o, c) * (1 - wd)
        out.append({"open": o, "high": h, "low": l, "close": c, "ts": ts})
        price = c
        ts += minutes_per_bar * 60_000
    return out

def aggregate(fine_bars, n):
    """Group fine_bars into n-bar candles."""
    out = []
    for i in range(0, len(fine_bars) - n + 1, n):
        group = fine_bars[i:i+n]
        out.append({
            "open":  group[0]["open"],
            "high":  max(b["high"] for b in group),
            "low":   min(b["low"]  for b in group),
            "close": group[-1]["close"],
            "ts":    group[0]["ts"],
            "ltf_bars": group,          # keep sub-bars for LTF confirmation
        })
    return out


# ── HTF pattern detection ──────────────────────────────────────────────────────
@dataclass
class HTF_Filter:
    min_c1_body_ratio: float = 0.0
    min_c2_ext_pct:    float = 0.0
    c2_close_strong:   bool  = False
    c2_engulfs_c1:     bool  = False


def matches_htf(c1, c2, direction, f: HTF_Filter):
    body  = abs(c1["close"] - c1["open"])
    rng   = c1["high"] - c1["low"] or 1e-9
    rng2  = c2["high"] - c2["low"] or 1e-9

    if f.min_c1_body_ratio and body / rng < f.min_c1_body_ratio:
        return False

    if direction == "bear":
        if not (c1["close"] > c1["open"]): return False
        if c2["high"] < c1["high"]:        return False
        if not (c2["close"] < c2["open"]): return False
        ext = (c2["high"] - c1["high"]) / c1["close"] * 100
        sc  = (c2["close"] - c2["low"]) / rng2 <= 0.40
        eng = c2["close"] <= c1["open"]
    else:
        if not (c1["close"] < c1["open"]): return False
        if c2["low"] > c1["low"]:          return False
        if not (c2["close"] > c2["open"]): return False
        ext = (c1["low"] - c2["low"]) / c1["close"] * 100
        sc  = (c2["high"] - c2["close"]) / rng2 <= 0.40
        eng = c2["close"] >= c1["open"]

    if f.min_c2_ext_pct  and ext < f.min_c2_ext_pct:  return False
    if f.c2_close_strong and not sc:                   return False
    if f.c2_engulfs_c1   and not eng:                  return False
    return True


# ── LTF confirmation ───────────────────────────────────────────────────────────
def ltf_confirm(c3_ltf_bars, direction, conf_type, conf_bars):
    """
    conf_type:
      'color'    — first N ltf bars must be majority in predicted direction
      'momentum' — ≥ conf_bars of first N ltf bars in direction
      'first'    — just the very first ltf bar must match direction
      'ema'      — ltf price must be below/above 20-bar EMA computed on sub-bars
      'none'     — no confirmation (baseline)
    """
    n = min(conf_bars, len(c3_ltf_bars))
    bars = c3_ltf_bars[:n]

    if conf_type == "none":
        return True

    if conf_type == "first":
        b = c3_ltf_bars[0]
        if direction == "bear": return b["close"] < b["open"]
        else:                   return b["close"] > b["open"]

    if conf_type == "color":
        # majority of first N bars in direction
        count = sum(1 for b in bars if
                    (direction == "bear" and b["close"] < b["open"]) or
                    (direction == "bull" and b["close"] > b["open"]))
        return count > n / 2

    if conf_type == "momentum":
        # last bar of look-back closes strongly in direction
        b = bars[-1]
        rng = b["high"] - b["low"] or 1e-9
        if direction == "bear":
            return b["close"] < b["open"] and (b["close"] - b["low"]) / rng < 0.40
        else:
            return b["close"] > b["open"] and (b["high"] - b["close"]) / rng < 0.40

    return True


# ── Full backtest ──────────────────────────────────────────────────────────────
def backtest(htf_candles, hf: HTF_Filter, conf_type, conf_bars):
    total = wins = 0
    for i in range(len(htf_candles) - 2):
        c1, c2, c3 = htf_candles[i], htf_candles[i+1], htf_candles[i+2]

        # check that c3 has sub-bars (for LTF confirmation)
        c3_ltf = c3.get("ltf_bars", [c3])

        for direction in ("bear", "bull"):
            if not matches_htf(c1, c2, direction, hf):
                continue

            # LTF confirmation
            if conf_type != "none" and len(c3_ltf) < 1:
                continue
            if not ltf_confirm(c3_ltf, direction, conf_type, conf_bars):
                continue

            # Outcome: did C3 close in predicted direction?
            c3_red   = c3["close"] < c3["open"]
            c3_green = c3["close"] > c3["open"]
            win = (direction == "bear" and c3_red) or \
                  (direction == "bull" and c3_green)

            total += 1
            wins  += win

    return total, wins


# ── Sweep ──────────────────────────────────────────────────────────────────────
HTF_GRID = {
    "min_c1_body_ratio": [0.0, 0.40, 0.55, 0.70],
    "min_c2_ext_pct":    [0.0, 0.05, 0.15, 0.25],
    "c2_close_strong":   [False, True],
    "c2_engulfs_c1":     [False, True],
}

CONF_GRID = [
    ("none",     1),
    ("first",    1),
    ("color",    1),
    ("color",    2),
    ("color",    3),
    ("momentum", 1),
    ("momentum", 2),
    ("momentum", 3),
]


def run_sweep(htf_candles, label):
    keys   = list(HTF_GRID.keys())
    combos = list(iproduct(*HTF_GRID.values()))
    results = []

    for combo in combos:
        hf = HTF_Filter(**dict(zip(keys, combo)))
        for conf_type, conf_bars in CONF_GRID:
            total, wins = backtest(htf_candles, hf, conf_type, conf_bars)
            if total < MIN_OCC:
                continue
            wr = wins / total * 100
            results.append({
                "hf": hf, "conf_type": conf_type, "conf_bars": conf_bars,
                "total": total, "wins": wins, "wr": wr,
            })

    return results


# ── Print ──────────────────────────────────────────────────────────────────────
def htf_label(hf):
    p = []
    if hf.min_c1_body_ratio: p.append(f"c1q≥{hf.min_c1_body_ratio}")
    if hf.min_c2_ext_pct:    p.append(f"ext≥{hf.min_c2_ext_pct}%")
    if hf.c2_close_strong:   p.append("c2str")
    if hf.c2_engulfs_c1:     p.append("engulf")
    return "+".join(p) or "nofilter"

def conf_label(ct, cb):
    if ct == "none":   return "NO CONFIRM"
    if ct == "first":  return "1st-LTF-bar"
    return f"{ct}({cb}bars)"

def print_results(results, htf_name, ltf_name, src, n_htf_bars):
    by_wr     = sorted(results, key=lambda r: r["wr"], reverse=True)
    by_trades = sorted([r for r in results if r["wr"] > 52], key=lambda r: r["total"], reverse=True)
    baseline  = next((r for r in results
                      if htf_label(r["hf"]) == "nofilter" and r["conf_type"] == "none"), None)

    print(f"\n{'='*78}")
    print(f"  HTF={htf_name}  LTF={ltf_name}  |  {src}  |  {n_htf_bars:,} HTF bars")
    if baseline:
        print(f"  Baseline (no filters, no confirm): {baseline['wr']:.1f}% WR  |  {baseline['total']:,} setups")
    print(f"  Results ≥{MIN_OCC} setups: {len(results)}   |   WR>52%: {len([r for r in results if r['wr']>52])}")
    print(f"{'='*78}")

    # Top 15 by WR
    print(f"\n  ── TOP 15 BY WIN RATE ──")
    print(f"  {'#':>3}  {'WR%':>6}  {'Setups':>7}  {'Confirm':>14}  HTF Filters")
    print("  " + "-"*74)
    for i, r in enumerate(by_wr[:15], 1):
        print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  "
              f"{conf_label(r['conf_type'], r['conf_bars']):>14}  {htf_label(r['hf'])}")

    # Top 15 by trades with WR>52%
    if by_trades:
        print(f"\n  ── MOST SETUPS WITH WR > 52% (Polymarket viable) ──")
        print(f"  {'#':>3}  {'WR%':>6}  {'Setups':>7}  {'Confirm':>14}  HTF Filters")
        print("  " + "-"*74)
        for i, r in enumerate(by_trades[:15], 1):
            print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  "
                  f"{conf_label(r['conf_type'], r['conf_bars']):>14}  {htf_label(r['hf'])}")

    # Best single recommendation
    best = by_wr[0] if by_wr else None
    if best:
        print(f"\n  BEST SETUP ON {htf_name}:")
        print(f"    WR         = {best['wr']:.1f}%")
        print(f"    Setups     = {best['total']:,}")
        print(f"    HTF filter = {htf_label(best['hf'])}")
        print(f"    Confirm    = {conf_label(best['conf_type'], best['conf_bars'])} on {ltf_name}")
        print(f"    Poly edge  = {best['wr'] - 52.5:.1f}% above breakeven (vs 52.5% needed)")


# ── Main ───────────────────────────────────────────────────────────────────────

CONFIGS = [
    # (htf_interval, htf_min, ltf_min, ltf_per_htf, sim_htf_bars)
    ("1h",  60,  5,  12, 25_000),   # 1h signal, 5m confirm
    ("1h",  60,  15,  4, 25_000),   # 1h signal, 15m confirm
    ("4h", 240,  15, 16, 20_000),   # 4h signal, 15m confirm
    ("4h", 240,  60,  4, 20_000),   # 4h signal, 1h confirm
]

REAL_DAYS = {"1h": 730, "4h": 1000}

def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    total_combos = len(list(iproduct(*HTF_GRID.values()))) * len(CONF_GRID)
    print(f"MTF Edge Finder for Polymarket  |  {mode}  |  {total_combos} combos per setup\n")
    print(f"  Breakeven WR on Polymarket ≈ 52.5%  (after ~5% platform fee)\n")

    for htf_int, htf_min, ltf_min, ltf_per_htf, sim_htf in CONFIGS:
        ltf_int = f"{ltf_min}m"
        htf_int_str = htf_int

        htf_candles = None

        if USE_REAL:
            try:
                # fetch HTF
                htf_raw = fetch_binance("BTCUSDT", htf_int_str, REAL_DAYS[htf_int_str])
                # fetch LTF
                ltf_raw = fetch_binance("BTCUSDT", ltf_int, REAL_DAYS[htf_int_str])

                # index ltf by timestamp for lookup
                ltf_idx = {}
                for b in ltf_raw:
                    ltf_idx[b["ts"]] = b

                # attach LTF bars to each HTF candle
                ltf_ms = ltf_min * 60_000
                htf_candles = []
                for hc in htf_raw:
                    subs = []
                    t = hc["ts"]
                    for _ in range(ltf_per_htf):
                        if t in ltf_idx:
                            subs.append(ltf_idx[t])
                        t += ltf_ms
                    hc["ltf_bars"] = subs
                    htf_candles.append(hc)

                src = f"Binance {REAL_DAYS[htf_int_str]}d"
            except Exception as e:
                print(f"  [{htf_int_str}/{ltf_int}] Binance error: {e} — using synthetic.")

        if htf_candles is None:
            # generate LTF-granularity data, then aggregate
            total_fine = sim_htf * ltf_per_htf
            fine = gen_fine(ltf_min, total_fine)
            htf_candles = aggregate(fine, ltf_per_htf)
            src = f"Synthetic ~{sim_htf} HTF bars"

        print(f"  [{htf_int_str} / {ltf_int}] sweeping {total_combos} combos …", end=" ", flush=True)
        results = run_sweep(htf_candles, f"{htf_int_str}/{ltf_int}")
        print("done")
        print_results(results, htf_int_str, ltf_int, src, len(htf_candles))

    print()


if __name__ == "__main__":
    main()
