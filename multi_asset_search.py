"""
Multi-asset C1/C2/C3 signal search: BTC + ETH + SOL
Find setups that give 3-4 trades per day across all 3 assets.

Entry = C3 OPEN (50 cents on Polymarket).
All signals computed from C2 and earlier — zero lookahead.

Usage:
  python multi_asset_search.py           # synthetic
  python multi_asset_search.py --real    # real Binance (blocked in sandbox)
  python multi_asset_search.py --tf 30m  # single timeframe
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from itertools import combinations


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
TF_ONLY  = next((args[i+1] for i, a in enumerate(args) if a == "--tf" and i+1<len(args)), None)
MIN_OCC  = 40
TOP_N    = 20


# ── Assets ─────────────────────────────────────────────────────────────────────
def _sigma(minutes, annual_vol):
    return math.sqrt(minutes / 525_600) * annual_vol

ASSETS = {
    "BTC": {"annual_vol": 0.85, "symbol": "BTCUSDT",  "seed": 42,  "start_price": 65_000},
    "ETH": {"annual_vol": 0.95, "symbol": "ETHUSDT",  "seed": 77,  "start_price": 3_500},
    "SOL": {"annual_vol": 1.10, "symbol": "SOLUSDT",  "seed": 111, "start_price": 150},
}

CONFIGS = {
    "30m": {"minutes": 30,  "sim_bars": 30_000, "real_days": 500,  "bpd": 48},
    "1h":  {"minutes": 60,  "sim_bars": 30_000, "real_days": 730,  "bpd": 24},
    "2h":  {"minutes": 120, "sim_bars": 25_000, "real_days": 1000, "bpd": 12},
    "4h":  {"minutes": 240, "sim_bars": 25_000, "real_days": 1000, "bpd": 6},
}


# ── Data ───────────────────────────────────────────────────────────────────────
def fetch_binance(symbol, interval, days):
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    out, cur = [], start_ms
    print(f"    Fetching {days}d {interval} {symbol} …", end=" ", flush=True)
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
                        "vol":  float(row[5])})
        cur = batch[-1][0] + 1
        if len(batch) < 1000: break
        time.sleep(0.08)
    print(f"{len(out):,}")
    return out


def synthetic(n, sigma, seed, start_price):
    rng, price = random.Random(seed), float(start_price)
    base_vol = 500.0
    out = []
    for _ in range(n):
        ret = rng.gauss(0, sigma)
        o, c = price, price * math.exp(ret)
        wu = abs(rng.gauss(0, sigma * 0.55))
        wd = abs(rng.gauss(0, sigma * 0.55))
        h = max(o, c) * (1 + wu)
        l = min(o, c) * (1 - wd)
        size = abs(c - o) / o
        vol = base_vol * math.exp(rng.gauss(0, 0.5)) * (1 + size * 20)
        out.append({"open": o, "high": h, "low": l, "close": c, "vol": vol})
        price = c
    return out


# ── Indicators ─────────────────────────────────────────────────────────────────
def atr(bars, i, n=14):
    if i < n: return None
    s = 0
    for j in range(i - n + 1, i + 1):
        s += max(bars[j]["high"] - bars[j]["low"],
                 abs(bars[j]["high"] - bars[j-1]["close"]),
                 abs(bars[j]["low"]  - bars[j-1]["close"]))
    return s / n

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
def extract(bars, i, direction):
    if i < 2: return None
    c0_idx = i - 2   # C1
    c1_idx = i - 1   # C2

    c1, c2 = bars[c0_idx], bars[c1_idx]
    bear = direction == "bear"

    c1_green = c1["close"] > c1["open"]
    c1_red   = c1["close"] < c1["open"]
    c2_red   = c2["close"] < c2["open"]
    c2_green = c2["close"] > c2["open"]

    if bear:
        if not c1_green or c2["high"] < c1["high"] or not c2_red: return None
    else:
        if not c1_red or c2["low"] > c1["low"] or not c2_green: return None

    c1_body  = abs(c1["close"] - c1["open"])
    c1_range = c1["high"] - c1["low"] or 1e-9
    c2_body  = abs(c2["close"] - c2["open"])
    c2_range = c2["high"] - c2["low"] or 1e-9

    a14    = atr(bars, c1_idx)     or 1e-9
    a14_old= atr(bars, c1_idx - 5) if c1_idx >= 5 else None
    avg_v  = avg_vol(bars, c1_idx) or 1e-9
    e20    = ema(bars, c1_idx, 20)
    e50    = ema(bars, c1_idx, 50)
    r_high = recent_high(bars, c1_idx, 20)
    r_low  = recent_low(bars,  c1_idx, 20)

    trend_red   = sum(1 for j in range(max(0, c0_idx-3), c0_idx)
                      if bars[j]["close"] < bars[j]["open"])
    trend_green = sum(1 for j in range(max(0, c0_idx-3), c0_idx)
                      if bars[j]["close"] > bars[j]["open"])

    c2_range_atr = c2_range / a14
    c2_body_atr  = c2_body  / a14
    c2_vol_spike = c2["vol"] / avg_v

    if bear:
        near_extreme  = abs(c2["high"] - r_high) / r_high < 0.003
        trend_aligned = trend_green >= 2
        counter_trend = trend_red   >= 2
    else:
        near_extreme  = abs(c2["low"] - r_low) / r_low < 0.003
        trend_aligned = trend_red   >= 2
        counter_trend = trend_green >= 2

    c3_open = c2["close"]
    vs_ema20 = (e20 is not None) and (
        (bear and c3_open < e20) or (not bear and c3_open > e20))
    vs_ema50 = (e50 is not None) and (
        (bear and c3_open < e50) or (not bear and c3_open > e50))

    vol_last3_inc = (c0_idx >= 2 and
                     bars[c0_idx-2]["vol"] < bars[c0_idx-1]["vol"] < c1["vol"])

    return {
        "engulf":        (bear and c2["close"] <= c1["open"]) or
                         (not bear and c2["close"] >= c1["open"]),
        "c2_strong_cl":  (bear and (c2["close"] - c2["low"])   / c2_range < 0.35) or
                         (not bear and (c2["high"] - c2["close"]) / c2_range < 0.35),
        "c1_qual_hi":    c1_body / c1_range > 0.55,
        "c1_qual_vhi":   c1_body / c1_range > 0.70,
        "c2_ext_big":    (bear  and (c2["high"] - c1["high"]) / c1["close"] > 0.001) or
                         (not bear and (c1["low"] - c2["low"]) / c1["close"] > 0.001),
        "c2_rng_1_5atr": c2_range_atr > 1.5,
        "c2_rng_2atr":   c2_range_atr > 2.0,
        "c2_body_1atr":  c2_body_atr  > 1.0,
        "atr_expanding": (a14_old is not None) and (a14 > a14_old),
        "vol_spike_1_5x":c2_vol_spike > 1.5,
        "vol_spike_2x":  c2_vol_spike > 2.0,
        "c2_vol_gt_c1":  c2["vol"] > c1["vol"] * 1.2,
        "vol_trend_up":  vol_last3_inc,
        "near_extreme":  near_extreme,
        "vs_ema20":      vs_ema20,
        "vs_ema50":      vs_ema50,
        "trend_aligned": trend_aligned,
        "counter_trend": counter_trend,
    }


def build_matrix(bars):
    records = []
    for i in range(2, len(bars) - 1):
        c3 = bars[i + 1]
        for direction in ("bear", "bull"):
            feats = extract(bars, i, direction)
            if feats is None: continue
            c3_red   = c3["close"] < c3["open"]
            c3_green = c3["close"] > c3["open"]
            win = (direction == "bear" and c3_red) or \
                  (direction == "bull" and c3_green)
            records.append((feats, win))
    return records


# ── Sweep ──────────────────────────────────────────────────────────────────────
ALL_SIGNALS = [
    "engulf", "c2_strong_cl", "c1_qual_hi", "c1_qual_vhi", "c2_ext_big",
    "c2_rng_1_5atr", "c2_rng_2atr", "c2_body_1atr", "atr_expanding",
    "vol_spike_1_5x", "vol_spike_2x", "c2_vol_gt_c1", "vol_trend_up",
    "near_extreme", "vs_ema20", "vs_ema50", "trend_aligned", "counter_trend",
]


def eval_filter(records, required):
    total = wins = 0
    for feats, outcome in records:
        if all(feats[s] for s in required):
            total += 1
            wins  += outcome
    return total, wins


def sweep(records):
    results = []

    t, w = eval_filter(records, set())
    if t >= MIN_OCC:
        results.append({"filters": frozenset(), "total": t, "wins": w, "wr": w/t*100})

    for s in ALL_SIGNALS:
        t, w = eval_filter(records, {s})
        if t >= MIN_OCC:
            results.append({"filters": frozenset({s}), "total": t, "wins": w, "wr": w/t*100})

    for s1, s2 in combinations(ALL_SIGNALS, 2):
        t, w = eval_filter(records, {s1, s2})
        if t >= MIN_OCC:
            results.append({"filters": frozenset({s1, s2}), "total": t, "wins": w, "wr": w/t*100})

    top5 = {list(r["filters"])[0]
            for r in sorted([r for r in results if len(r["filters"]) == 1],
                            key=lambda r: r["wr"], reverse=True)[:5]}
    for anchor in top5:
        for s1, s2 in combinations(ALL_SIGNALS, 2):
            if anchor in {s1, s2}: continue
            filt = frozenset({anchor, s1, s2})
            if any(r["filters"] == filt for r in results): continue
            t, w = eval_filter(records, filt)
            if t >= MIN_OCC:
                results.append({"filters": filt, "total": t, "wins": w, "wr": w/t*100})

    return results


# ── Print ──────────────────────────────────────────────────────────────────────
def print_results(results, tf, calendar_days):
    by_wr   = sorted(results, key=lambda r: r["wr"], reverse=True)
    above52 = [r for r in results if r["wr"] > 52.5]
    by_vol  = sorted(above52, key=lambda r: r["total"], reverse=True)

    baseline = next((r for r in results if not r["filters"]), None)

    def spd(n):  # signals per day
        return n / calendar_days

    target_lo, target_hi = 3.0, 4.0
    sweet_spot = sorted(
        [r for r in above52
         if target_lo <= spd(r["total"]) <= target_hi * 1.5],
        key=lambda r: r["wr"], reverse=True
    )

    print(f"\n{'='*84}")
    print(f"  {tf} — BTC + ETH + SOL combined  |  {calendar_days:.0f} calendar days")
    if baseline:
        b = baseline
        print(f"  Baseline: {b['wr']:.1f}% WR  |  {b['total']:,} setups  |  "
              f"{spd(b['total']):.1f} signals/day")
    print(f"  Combos tested: {len(results)}   Above 52.5% WR: {len(above52)}")
    print(f"  Polymarket breakeven ≈ 52.5%")
    print(f"{'='*84}")

    hdr = f"  {'#':>3}  {'WR%':>6}  {'Sig/day':>8}  {'Setups':>7}  Signals"
    sep = "  " + "-"*80

    print(f"\n  ── TOP {TOP_N} BY WIN RATE ──")
    print(hdr); print(sep)
    for i, r in enumerate(by_wr[:TOP_N], 1):
        fstr = " + ".join(sorted(r["filters"])) or "no filters"
        print(f"  {i:>3}  {r['wr']:>6.1f}  {spd(r['total']):>8.2f}  {r['total']:>7,}  {fstr}")

    if sweet_spot:
        print(f"\n  ── SWEET SPOT: {target_lo:.0f}–{target_hi*1.5:.0f} signals/day, WR > 52.5% ──")
        print(hdr); print(sep)
        for i, r in enumerate(sweet_spot[:TOP_N], 1):
            fstr = " + ".join(sorted(r["filters"])) or "no filters"
            print(f"  {i:>3}  {r['wr']:>6.1f}  {spd(r['total']):>8.2f}  {r['total']:>7,}  {fstr}")

    if by_vol:
        print(f"\n  ── MOST SIGNALS/DAY WITH WR > 52.5% ──")
        print(hdr); print(sep)
        for i, r in enumerate(by_vol[:TOP_N], 1):
            fstr = " + ".join(sorted(r["filters"])) or "no filters"
            print(f"  {i:>3}  {r['wr']:>6.1f}  {spd(r['total']):>8.2f}  {r['total']:>7,}  {fstr}")

    singles = sorted([r for r in results if len(r["filters"]) == 1],
                     key=lambda r: r["wr"], reverse=True)
    if singles and baseline:
        print(f"\n  ── SIGNAL CONTRIBUTION (vs baseline {baseline['wr']:.1f}%) ──")
        print(f"  {'Signal':<22}  {'WR%':>6}  {'Edge':>6}  {'Sig/day':>8}  {'Setups':>7}")
        print("  " + "-"*60)
        for r in singles:
            sig  = list(r["filters"])[0]
            edge = r["wr"] - baseline["wr"]
            print(f"  {sig:<22}  {r['wr']:>6.1f}  {edge:>+6.1f}  "
                  f"{spd(r['total']):>8.2f}  {r['total']:>7,}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    tfs  = [TF_ONLY] if TF_ONLY else list(CONFIGS.keys())

    print(f"Multi-Asset Signal Search  |  BTC + ETH + SOL  |  {mode}")
    print(f"Goal: 3-4 trades per day  |  Entry at C3 open (50c Polymarket)\n")

    for tf in tfs:
        cfg = CONFIGS.get(tf)
        if not cfg:
            print(f"  Unknown TF: {tf}"); continue

        all_records = []
        calendar_days = cfg["sim_bars"] / cfg["bpd"]

        print(f"  [{tf}] {calendar_days:.0f} calendar days per asset")
        for asset, acfg in ASSETS.items():
            sigma = _sigma(cfg["minutes"], acfg["annual_vol"])

            bars = None
            if USE_REAL:
                try:
                    bars = fetch_binance(acfg["symbol"], tf, cfg["real_days"])
                except Exception as e:
                    print(f"    [{asset}] Binance error — synthetic. ({e})")

            if bars is None:
                bars = synthetic(cfg["sim_bars"], sigma, acfg["seed"], acfg["start_price"])
                print(f"    [{asset}] synthetic {cfg['sim_bars']:,} bars  σ/bar={sigma:.4f}")

            recs = build_matrix(bars)
            print(f"    [{asset}] {len(recs):,} setups extracted")
            all_records.extend(recs)

        print(f"  [{tf}] total {len(all_records):,} setups  |  sweeping …", end=" ", flush=True)
        results = sweep(all_records)
        print(f"done  ({len(results)} combos)")
        print_results(results, tf, calendar_days)
        print()


if __name__ == "__main__":
    main()
