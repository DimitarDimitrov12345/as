"""
MTF C1/C2/C3 — HONEST Polymarket edition.

Entry = C3 OPEN (50 cents on Polymarket). No C3 bars seen before betting.
Confirmation = last N LTF bars of C2 (visible BEFORE C3 opens).

Why the previous version was wrong:
  "color(2bars) of C3" = you already saw C3 move → Polymarket price is 65+ cents,
  NOT 50 cents. That's not an entry at 50 cents, that's buying at 65 cents.

This version only uses information available at C3 open:
  - C1 and C2 are complete (HTF pattern known)
  - Last few LTF sub-bars of C2 confirm momentum

Usage:
  python mtf_edge.py          # synthetic
  python mtf_edge.py --real   # real Binance data
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from itertools import product as iproduct


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
MIN_OCC  = 50


# ── Data gen / fetch ───────────────────────────────────────────────────────────
def _sigma(minutes): return math.sqrt(minutes / 525_600) * 0.85

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

def gen_fine(min_per_bar, n_bars, seed=42):
    sig = _sigma(min_per_bar)
    rng, price, ts = random.Random(seed), 65_000.0, 0
    out = []
    for _ in range(n_bars):
        ret = rng.gauss(0, sig)
        o, c = price, price * math.exp(ret)
        h = max(o, c) * (1 + abs(rng.gauss(0, sig * 0.55)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sig * 0.55)))
        out.append({"open": o, "high": h, "low": l, "close": c, "ts": ts})
        price = c
        ts += min_per_bar * 60_000
    return out

def aggregate(fine, n):
    """Aggregate into n-bar candles, attaching sub-bars."""
    out = []
    for i in range(0, len(fine) - n + 1, n):
        g = fine[i:i+n]
        out.append({
            "open":  g[0]["open"], "high": max(b["high"] for b in g),
            "low":   min(b["low"]  for b in g), "close": g[-1]["close"],
            "ts":    g[0]["ts"],   "sub":  g,
        })
    return out


# ── HTF filter ─────────────────────────────────────────────────────────────────
@dataclass
class HTF_F:
    min_c1_body_ratio: float = 0.0
    min_c2_ext_pct:    float = 0.0
    c2_close_strong:   bool  = False
    c2_engulfs_c1:     bool  = False

def htf_matches(c1, c2, direction, f: HTF_F):
    body = abs(c1["close"] - c1["open"])
    rng  = c1["high"] - c1["low"] or 1e-9
    rng2 = c2["high"] - c2["low"] or 1e-9
    if f.min_c1_body_ratio and body / rng < f.min_c1_body_ratio:
        return False
    if direction == "bear":
        if c1["close"] <= c1["open"]:  return False
        if c2["high"]  <  c1["high"]:  return False
        if c2["close"] >= c2["open"]:  return False
        ext = (c2["high"] - c1["high"]) / c1["close"] * 100
        sc  = (c2["close"] - c2["low"]) / rng2 <= 0.40
        eng = c2["close"] <= c1["open"]
    else:
        if c1["close"] >= c1["open"]:  return False
        if c2["low"]   >  c1["low"]:   return False
        if c2["close"] <= c2["open"]:  return False
        ext = (c1["low"] - c2["low"]) / c1["close"] * 100
        sc  = (c2["high"] - c2["close"]) / rng2 <= 0.40
        eng = c2["close"] >= c1["open"]
    if f.min_c2_ext_pct  and ext < f.min_c2_ext_pct:  return False
    if f.c2_close_strong and not sc:                   return False
    if f.c2_engulfs_c1   and not eng:                  return False
    return True


# ── LTF pre-confirmation (uses C2 sub-bars only — all known before C3 opens) ──
def ltf_preconfirm(c2_subs, direction, conf_type, n_bars):
    """
    All checks use C2's sub-bars. Nothing from C3.
    Returns True if the confirmation condition is met.
    """
    if conf_type == "none":
        return True

    subs = c2_subs[-n_bars:] if len(c2_subs) >= n_bars else c2_subs
    if not subs:
        return True

    if conf_type == "last_bar_color":
        # Last LTF bar of C2 must be in direction (reversal bar closing strong)
        b = subs[-1]
        if direction == "bear": return b["close"] < b["open"]
        else:                   return b["close"] > b["open"]

    if conf_type == "majority_color":
        # Majority of last N bars in direction
        count = sum(1 for b in subs if
                    (direction == "bear" and b["close"] < b["open"]) or
                    (direction == "bull" and b["close"] > b["open"]))
        return count > len(subs) / 2

    if conf_type == "last_strong":
        # Last bar of C2 closes in direction AND closes in top/bottom 30% of its range
        b = subs[-1]
        rng = b["high"] - b["low"] or 1e-9
        if direction == "bear":
            return b["close"] < b["open"] and (b["close"] - b["low"]) / rng < 0.30
        else:
            return b["close"] > b["open"] and (b["high"] - b["close"]) / rng < 0.30

    if conf_type == "momentum_slope":
        # Each successive bar's close is lower (bear) or higher (bull) than previous
        if len(subs) < 2: return True
        closes = [b["close"] for b in subs]
        if direction == "bear": return all(closes[i] > closes[i+1] for i in range(len(closes)-1))
        else:                   return all(closes[i] < closes[i+1] for i in range(len(closes)-1))

    if conf_type == "c2_close_position":
        # C2's last close must be in the weakest 25% of C2's total range
        c2_high = max(b["high"] for b in c2_subs)
        c2_low  = min(b["low"]  for b in c2_subs)
        c2_rng  = c2_high - c2_low or 1e-9
        last_close = subs[-1]["close"]
        if direction == "bear": return (last_close - c2_low) / c2_rng < 0.25
        else:                   return (c2_high - last_close) / c2_rng < 0.25

    return True


# ── Full backtest ──────────────────────────────────────────────────────────────
def backtest(htf_candles, hf: HTF_F, conf_type, n_bars):
    total = wins = 0
    for i in range(len(htf_candles) - 2):
        c1, c2, c3 = htf_candles[i], htf_candles[i+1], htf_candles[i+2]
        c2_subs = c2.get("sub", [c2])

        for direction in ("bear", "bull"):
            if not htf_matches(c1, c2, direction, hf):
                continue
            if not ltf_preconfirm(c2_subs, direction, conf_type, n_bars):
                continue

            c3_red   = c3["close"] < c3["open"]
            c3_green = c3["close"] > c3["open"]
            win = (direction == "bear" and c3_red) or \
                  (direction == "bull" and c3_green)
            total += 1
            wins  += win

    return total, wins


# ── Grid sweep ─────────────────────────────────────────────────────────────────
HTF_GRID = {
    "min_c1_body_ratio": [0.0, 0.40, 0.55, 0.70],
    "min_c2_ext_pct":    [0.0, 0.05, 0.15, 0.25],
    "c2_close_strong":   [False, True],
    "c2_engulfs_c1":     [False, True],
}

CONF_GRID = [
    ("none",             1),
    ("last_bar_color",   1),
    ("last_strong",      1),
    ("majority_color",   2),
    ("majority_color",   3),
    ("majority_color",   4),
    ("momentum_slope",   2),
    ("momentum_slope",   3),
    ("c2_close_position",1),
]

def sweep(htf_candles):
    keys   = list(HTF_GRID.keys())
    combos = list(iproduct(*HTF_GRID.values()))
    results = []
    for combo in combos:
        hf = HTF_F(**dict(zip(keys, combo)))
        for conf_type, n_bars in CONF_GRID:
            total, wins = backtest(htf_candles, hf, conf_type, n_bars)
            if total < MIN_OCC: continue
            wr = wins / total * 100
            results.append({"hf": hf, "conf_type": conf_type, "n_bars": n_bars,
                            "total": total, "wins": wins, "wr": wr})
    return results


# ── Labels ─────────────────────────────────────────────────────────────────────
def htf_lbl(hf):
    p = []
    if hf.min_c1_body_ratio: p.append(f"c1q≥{hf.min_c1_body_ratio}")
    if hf.min_c2_ext_pct:    p.append(f"ext≥{hf.min_c2_ext_pct}%")
    if hf.c2_close_strong:   p.append("c2str")
    if hf.c2_engulfs_c1:     p.append("engulf")
    return "+".join(p) or "nofilter"

def conf_lbl(ct, n):
    m = {"none": "NO-CONFIRM", "last_bar_color": f"c2-last-bar",
         "last_strong": "c2-last-strong", "majority_color": f"c2-maj({n}bars)",
         "momentum_slope": f"c2-slope({n}bars)", "c2_close_position": "c2-closepos"}
    return m.get(ct, ct)


# ── Print ──────────────────────────────────────────────────────────────────────
def print_results(results, htf_name, ltf_name, src, n_bars_htf):
    by_wr     = sorted(results, key=lambda r: r["wr"], reverse=True)
    above52   = [r for r in results if r["wr"] > 52.5]
    by_trades = sorted(above52, key=lambda r: r["total"], reverse=True)
    baseline  = next((r for r in results
                      if htf_lbl(r["hf"]) == "nofilter" and r["conf_type"] == "none"), None)

    print(f"\n{'='*80}")
    print(f"  HTF={htf_name}  LTF={ltf_name}  confirm=C2 sub-bars ONLY (honest 50c entry)")
    print(f"  {src}  |  {n_bars_htf:,} HTF bars  |  min {MIN_OCC} setups")
    if baseline:
        b = baseline
        print(f"  Baseline (no filters, no confirm): {b['wr']:.1f}% WR  |  {b['total']:,} setups")
    print(f"  Combos above 52.5% breakeven: {len(above52)}")
    print(f"{'='*80}")

    hdr = f"  {'#':>3}  {'WR%':>6}  {'Setups':>7}  {'C2 Confirm':>20}  HTF Filters"
    sep = "  " + "-"*76

    print(f"\n  ── TOP 15 BY WIN RATE (honest — all info available at C3 open) ──")
    print(hdr); print(sep)
    for i, r in enumerate(by_wr[:15], 1):
        print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  "
              f"{conf_lbl(r['conf_type'], r['n_bars']):>20}  {htf_lbl(r['hf'])}")

    if by_trades:
        print(f"\n  ── MOST SETUPS WITH WR > 52.5% (volume + edge) ──")
        print(hdr); print(sep)
        for i, r in enumerate(by_trades[:15], 1):
            print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  "
                  f"{conf_lbl(r['conf_type'], r['n_bars']):>20}  {htf_lbl(r['hf'])}")

    best = by_wr[0] if by_wr else None
    if best:
        print(f"\n  ★  BEST  ★")
        print(f"    WR         = {best['wr']:.1f}%")
        print(f"    Setups     = {best['total']:,}")
        print(f"    HTF        = {htf_lbl(best['hf'])}")
        print(f"    Confirm    = {conf_lbl(best['conf_type'], best['n_bars'])}")
        print(f"    Edge       = +{best['wr'] - 52.5:.1f}% above 52.5% breakeven")
        print(f"    Entry      = C3 OPEN  ← Polymarket price ≈ 50 cents ✓")


# ── Configs (HTF, LTF_min, ltf_per_htf, sim_htf_bars, real_days) ──────────────
CONFIGS = [
    ("1h",  60,   5,  12, 25_000, 730),
    ("1h",  60,  15,   4, 25_000, 730),
    ("4h", 240,  15,  16, 20_000, 1000),
    ("4h", 240,  60,   4, 20_000, 1000),
]

def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    n_combos = len(list(iproduct(*HTF_GRID.values()))) * len(CONF_GRID)
    print(f"MTF Edge Finder — HONEST 50c Entry  |  {mode}  |  {n_combos} combos each\n")
    print(f"  Breakeven = 52.5% WR  (Polymarket ~5% fee on wins)\n")

    for htf_name, htf_min, ltf_min, ltf_per_htf, sim_htf, real_days in CONFIGS:
        ltf_name = f"{ltf_min}m"
        htf_candles = None

        if USE_REAL:
            try:
                htf_raw = fetch_binance("BTCUSDT", htf_name, real_days)
                ltf_raw = fetch_binance("BTCUSDT", ltf_name, real_days)
                ltf_by_ts = {b["ts"]: b for b in ltf_raw}
                ltf_ms = ltf_min * 60_000
                htf_candles = []
                for hc in htf_raw:
                    subs = []
                    t = hc["ts"]
                    for _ in range(ltf_per_htf):
                        if t in ltf_by_ts: subs.append(ltf_by_ts[t])
                        t += ltf_ms
                    hc["sub"] = subs
                    htf_candles.append(hc)
                src = f"Binance {real_days}d"
            except Exception as e:
                print(f"  [{htf_name}/{ltf_name}] Binance error ({e}) — synthetic.")

        if htf_candles is None:
            fine = gen_fine(ltf_min, sim_htf * ltf_per_htf)
            htf_candles = aggregate(fine, ltf_per_htf)
            src = f"Synthetic ~{sim_htf} bars"

        total_combos = n_combos
        print(f"  [{htf_name}/{ltf_name}] sweeping {total_combos} combos …", end=" ", flush=True)
        results = sweep(htf_candles)
        print("done")
        print_results(results, htf_name, ltf_name, src, len(htf_candles))

    print()

if __name__ == "__main__":
    main()
