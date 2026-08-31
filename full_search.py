"""
Comprehensive candle direction predictor for Polymarket.
Entry = C3 OPEN (50 cents). All signals computed from C2 and earlier ONLY.

Signals tested:
  PATTERN  — C1/C2/C3 engulf, extension, close strength, C1 quality
  ATR      — C2 range as multiple of ATR14, ATR expanding
  VOLUME   — volume spike, C2 vol > C1 vol, volume trend
  CONTEXT  — near N-bar extreme, price vs EMA20/50, trend alignment

Usage:
  python full_search.py           # synthetic (Binance blocked in sandbox)
  python full_search.py --real    # real Binance data
  python full_search.py --tf 1h   # single timeframe
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from collections import defaultdict
from itertools import combinations


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
TF_ONLY  = next((args[i+1] for i, a in enumerate(args) if a == "--tf" and i+1 < len(args)), None)
MIN_OCC  = 40
TOP_N    = 20


# ── Data ───────────────────────────────────────────────────────────────────────
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
                        "vol":  float(row[5]), "ts":   int(row[0])})
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.08)
    print(f"{len(out):,} bars")
    return out

def synthetic(n, sigma, seed=42):
    rng, price = random.Random(seed), 65_000.0
    base_vol = 500.0
    out = []
    for _ in range(n):
        ret = rng.gauss(0, sigma)
        o, c = price, price * math.exp(ret)
        wu = abs(rng.gauss(0, sigma * 0.55))
        wd = abs(rng.gauss(0, sigma * 0.55))
        h = max(o, c) * (1 + wu)
        l = min(o, c) * (1 - wd)
        # volume correlated with candle size
        candle_size = abs(c - o) / o
        vol = base_vol * math.exp(rng.gauss(0, 0.5)) * (1 + candle_size * 20)
        out.append({"open": o, "high": h, "low": l, "close": c, "vol": vol})
        price = c
    return out


# ── Indicators ─────────────────────────────────────────────────────────────────
def atr(bars, i, n=14):
    if i < n: return None
    tr_sum = 0
    for j in range(i - n + 1, i + 1):
        tr = max(bars[j]["high"] - bars[j]["low"],
                 abs(bars[j]["high"] - bars[j-1]["close"]),
                 abs(bars[j]["low"]  - bars[j-1]["close"]))
        tr_sum += tr
    return tr_sum / n

def ema(bars, i, n):
    if i < n - 1: return None
    k = 2 / (n + 1)
    val = sum(bars[j]["close"] for j in range(n)) / n
    for j in range(n, i + 1):
        val = bars[j]["close"] * k + val * (1 - k)
    return val

def avg_vol(bars, i, n=20):
    if i < n: return None
    return sum(bars[j]["vol"] for j in range(i - n + 1, i + 1)) / n

def recent_high(bars, i, n=20):
    if i < n: return bars[i]["high"]
    return max(bars[j]["high"] for j in range(i - n + 1, i + 1))

def recent_low(bars, i, n=20):
    if i < n: return bars[i]["low"]
    return min(bars[j]["low"] for j in range(i - n + 1, i + 1))


# ── Feature extraction ─────────────────────────────────────────────────────────
# All features computed AT i=2 (C2 index), using bars[0..i] only.
# Direction = "bear" or "bull"

def extract(bars, i, direction):
    """Returns a dict of signal booleans for bars[i-2], bars[i-1], bars[i] = C1, C2, C3."""
    if i < 2: return None
    c0_idx = i - 2   # C1
    c1_idx = i - 1   # C2
    # C3 = bars[i] — used only for outcome, not features

    c1, c2 = bars[c0_idx], bars[c1_idx]

    bear = direction == "bear"

    # ── 1. Base pattern requirement ────────────────────────────────────────────
    c1_green = c1["close"] > c1["open"]
    c1_red   = c1["close"] < c1["open"]
    c2_red   = c2["close"] < c2["open"]
    c2_green = c2["close"] > c2["open"]

    if bear:
        if not c1_green: return None   # C1 must be green for bearish
        if c2["high"] < c1["high"]: return None   # C2 must take C1 high
        if not c2_red: return None     # C2 must close red
    else:
        if not c1_red: return None     # C1 must be red for bullish
        if c2["low"] > c1["low"]: return None    # C2 must take C1 low
        if not c2_green: return None   # C2 must close green

    # From here, base pattern is met. Extract all signals.
    c1_body  = abs(c1["close"] - c1["open"])
    c1_range = c1["high"] - c1["low"] or 1e-9
    c2_body  = abs(c2["close"] - c2["open"])
    c2_range = c2["high"] - c2["low"] or 1e-9

    # ATR
    a14 = atr(bars, c1_idx) or 1e-9

    # Volume
    avg_v = avg_vol(bars, c1_idx) or 1e-9

    # EMAs (computed at C2)
    e20 = ema(bars, c1_idx, 20)
    e50 = ema(bars, c1_idx, 50)

    # Recent extremes (N=20, at C2)
    r_high = recent_high(bars, c1_idx, 20)
    r_low  = recent_low(bars,  c1_idx, 20)

    # Trend: last 3 candles before C1
    trend_red_count   = sum(1 for j in range(max(0, c0_idx-3), c0_idx)
                            if bars[j]["close"] < bars[j]["open"])
    trend_green_count = sum(1 for j in range(max(0, c0_idx-3), c0_idx)
                            if bars[j]["close"] > bars[j]["open"])

    # ATR signals
    c2_range_atr = c2_range / a14
    c2_body_atr  = c2_body  / a14

    # Volume signals
    c2_vol_spike  = c2["vol"] / avg_v
    c2_vol_gt_c1  = c2["vol"] > c1["vol"] * 1.2
    vol_last3_inc = (c0_idx >= 2 and
                     bars[c0_idx-2]["vol"] < bars[c0_idx-1]["vol"] < c1["vol"])

    # Proximity to recent extreme (C2 high/low within 0.3% of N-bar extreme)
    if bear:
        near_extreme = abs(c2["high"] - r_high) / r_high < 0.003
    else:
        near_extreme = abs(c2["low"] - r_low) / r_low < 0.003

    # Price vs EMA (C3 opens at C2 close — where is it?)
    c3_open = c2["close"]
    vs_ema20 = (e20 is not None) and (
        (bear and c3_open < e20) or (not bear and c3_open > e20))
    vs_ema50 = (e50 is not None) and (
        (bear and c3_open < e50) or (not bear and c3_open > e50))

    # Trend alignment (C1 is WITH recent trend → exhaustion trade)
    if bear:
        trend_aligned  = trend_green_count >= 2   # C1 extends green run
        counter_trend  = trend_red_count   >= 2   # C1 goes against red run
    else:
        trend_aligned  = trend_red_count   >= 2   # C1 extends red run
        counter_trend  = trend_green_count >= 2

    # Pattern signals
    engulf       = (bear and c2["close"] <= c1["open"]) or \
                   (not bear and c2["close"] >= c1["open"])
    c2_strong_cl = (bear and (c2["close"] - c2["low"])  / c2_range < 0.35) or \
                   (not bear and (c2["high"] - c2["close"]) / c2_range < 0.35)
    c1_quality   = c1_body / c1_range  # 0–1, higher = stronger C1
    c2_ext_big   = bear and (c2["high"] - c1["high"]) / c1["close"] > 0.001 or \
                   not bear and (c1["low"] - c2["low"]) / c1["close"] > 0.001

    # ATR expanding (C2 ATR14 > C2-5 ATR14)
    a14_old = atr(bars, c1_idx - 5) if c1_idx >= 5 else None
    atr_exp = (a14_old is not None) and (a14 > a14_old)

    return {
        # pattern
        "engulf":           engulf,
        "c2_strong_cl":     c2_strong_cl,
        "c1_qual_hi":       c1_quality > 0.55,
        "c1_qual_vhi":      c1_quality > 0.70,
        "c2_ext_big":       c2_ext_big,
        # atr
        "c2_rng_1_5atr":    c2_range_atr > 1.5,
        "c2_rng_2atr":      c2_range_atr > 2.0,
        "c2_body_1atr":     c2_body_atr > 1.0,
        "atr_expanding":    atr_exp,
        # volume
        "vol_spike_1_5x":   c2_vol_spike > 1.5,
        "vol_spike_2x":     c2_vol_spike > 2.0,
        "c2_vol_gt_c1":     c2_vol_gt_c1,
        "vol_trend_up":     vol_last3_inc,
        # context
        "near_extreme":     near_extreme,
        "vs_ema20":         vs_ema20,
        "vs_ema50":         vs_ema50,
        "trend_aligned":    trend_aligned,
        "counter_trend":    counter_trend,
    }


# ── Build feature matrix ───────────────────────────────────────────────────────
def build_matrix(bars):
    """Returns list of (features_dict, outcome_bool) for every valid setup."""
    records = []
    for i in range(2, len(bars) - 1):
        c3 = bars[i + 1]  # C3 is bars[i+1]
        for direction in ("bear", "bull"):
            feats = extract(bars, i, direction)
            if feats is None: continue
            c3_red   = c3["close"] < c3["open"]
            c3_green = c3["close"] > c3["open"]
            win = (direction == "bear" and c3_red) or \
                  (direction == "bull" and c3_green)
            records.append((feats, win))
    return records


# ── Sweep: test every individual signal + all pairs ───────────────────────────
ALL_SIGNALS = [
    "engulf", "c2_strong_cl", "c1_qual_hi", "c1_qual_vhi", "c2_ext_big",
    "c2_rng_1_5atr", "c2_rng_2atr", "c2_body_1atr", "atr_expanding",
    "vol_spike_1_5x", "vol_spike_2x", "c2_vol_gt_c1", "vol_trend_up",
    "near_extreme", "vs_ema20", "vs_ema50", "trend_aligned", "counter_trend",
]


def eval_filter(records, required_true):
    """required_true = set of signal names that must all be True."""
    total = wins = 0
    for feats, outcome in records:
        if all(feats[s] for s in required_true):
            total += 1
            wins  += outcome
    return total, wins


def sweep(records):
    results = []

    # baseline (no filter)
    t, w = eval_filter(records, set())
    if t >= MIN_OCC:
        results.append({"filters": frozenset(), "total": t, "wins": w,
                        "wr": w/t*100})

    # single signals
    for s in ALL_SIGNALS:
        t, w = eval_filter(records, {s})
        if t >= MIN_OCC:
            results.append({"filters": frozenset({s}), "total": t, "wins": w,
                            "wr": w/t*100})

    # pairs
    for s1, s2 in combinations(ALL_SIGNALS, 2):
        t, w = eval_filter(records, {s1, s2})
        if t >= MIN_OCC:
            results.append({"filters": frozenset({s1, s2}), "total": t, "wins": w,
                            "wr": w/t*100})

    # triples: only build on top-5 singles to keep it fast
    top5_singles = sorted(
        [r for r in results if len(r["filters"]) == 1],
        key=lambda r: r["wr"], reverse=True
    )[:5]
    top5_set = {list(r["filters"])[0] for r in top5_singles}

    for anchor in top5_set:
        for s1, s2 in combinations(ALL_SIGNALS, 2):
            if anchor in {s1, s2}: continue
            filt = frozenset({anchor, s1, s2})
            if any(r["filters"] == filt for r in results): continue
            t, w = eval_filter(records, filt)
            if t >= MIN_OCC:
                results.append({"filters": filt, "total": t, "wins": w,
                                "wr": w/t*100})

    return results


# ── Print ──────────────────────────────────────────────────────────────────────
def print_results(results, tf, src, n_bars):
    by_wr     = sorted(results, key=lambda r: r["wr"], reverse=True)
    above52   = [r for r in results if r["wr"] > 52.5]
    by_trades = sorted(above52, key=lambda r: r["total"], reverse=True)

    baseline = next((r for r in results if not r["filters"]), None)

    print(f"\n{'='*80}")
    print(f"  {tf}  |  {src}  |  {n_bars:,} bars  |  min {MIN_OCC} setups")
    if baseline:
        b = baseline
        print(f"  Baseline (no filters): {b['wr']:.1f}% WR  |  {b['total']:,} setups")
    print(f"  Combos tested: {len(results)}   Combos > 52.5%: {len(above52)}")
    print(f"{'='*80}")

    hdr = f"  {'#':>3}  {'WR%':>6}  {'Setups':>7}  Signals"
    sep = "  " + "-"*76

    print(f"\n  ── TOP {TOP_N} BY WIN RATE ──")
    print(hdr); print(sep)
    for i, r in enumerate(by_wr[:TOP_N], 1):
        fstr = " + ".join(sorted(r["filters"])) or "no filters"
        print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  {fstr}")

    if by_trades:
        print(f"\n  ── MOST SETUPS WITH WR > 52.5% (volume + edge) ──")
        print(hdr); print(sep)
        for i, r in enumerate(by_trades[:TOP_N], 1):
            fstr = " + ".join(sorted(r["filters"])) or "no filters"
            print(f"  {i:>3}  {r['wr']:>6.1f}  {r['total']:>7,}  {fstr}")

    # signal contribution table
    singles = sorted([r for r in results if len(r["filters"]) == 1],
                     key=lambda r: r["wr"], reverse=True)
    if singles and baseline:
        print(f"\n  ── INDIVIDUAL SIGNAL CONTRIBUTION (vs baseline {baseline['wr']:.1f}%) ──")
        print(f"  {'Signal':<22}  {'WR%':>6}  {'Edge':>6}  {'Setups':>7}")
        print("  " + "-"*50)
        for r in singles:
            sig = list(r["filters"])[0]
            edge = r["wr"] - baseline["wr"]
            print(f"  {sig:<22}  {r['wr']:>6.1f}  {edge:>+6.1f}  {r['total']:>7,}")


# ── Timeframe configs ──────────────────────────────────────────────────────────
CONFIGS = {
    "1h":  {"sigma": _sigma(60),  "sim_bars": 30_000, "real_days": 730},
    "4h":  {"sigma": _sigma(240), "sim_bars": 25_000, "real_days": 1000},
    "2h":  {"sigma": _sigma(120), "sim_bars": 25_000, "real_days": 1000},
    "30m": {"sigma": _sigma(30),  "sim_bars": 30_000, "real_days": 500},
}


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    tfs  = [TF_ONLY] if TF_ONLY else list(CONFIGS.keys())
    print(f"Full Signal Search  |  {mode}  |  Signals: {len(ALL_SIGNALS)}"
          f"  Pairs: {len(ALL_SIGNALS)*(len(ALL_SIGNALS)-1)//2}\n")
    print(f"  Breakeven on Polymarket ≈ 52.5% WR\n")

    for tf in tfs:
        cfg = CONFIGS.get(tf)
        if not cfg:
            print(f"  Unknown TF: {tf}")
            continue

        bars = None
        if USE_REAL:
            try:
                bars = fetch_binance("BTCUSDT", tf, cfg["real_days"])
                src  = f"Binance {cfg['real_days']}d"
            except Exception as e:
                print(f"  [{tf}] Binance error — synthetic. ({e})")

        if bars is None:
            bars = synthetic(cfg["sim_bars"], cfg["sigma"])
            src  = f"Synthetic {cfg['sim_bars']:,} bars"

        print(f"  [{tf}] extracting features …", end=" ", flush=True)
        records = build_matrix(bars)
        print(f"{len(records):,} setups  |  sweeping …", end=" ", flush=True)
        results = sweep(records)
        print("done")
        print_results(results, tf, src, len(bars))

    print()

if __name__ == "__main__":
    main()
