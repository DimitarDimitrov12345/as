"""
ORB Confluence Search — 9:30-9:45 NY range, 5m signal

Target: Predict COLOR of the next 5m bar after the first range breakout.
Win = next bar green (bull break) or red (bear break).
No SL / TP — pure Polymarket color guess.

Confluences tested:
  vol_spike     — breakout bar volume > 1.5× avg vol of range bars
  strong_close  — breakout bar body closes > 30% of bar's range beyond the level
  small_range   — opening range size < median range size (compressed range = bigger move)
  early_break   — breakout within first 12 bars after range (9:45-10:30)
  trend_into    — 2+ of last 3 pre-signal bars move in breakout direction
  big_extension — close extends > 25% of range_size beyond the boundary
  next_vol_ok   — target bar volume > avg range vol (confirms momentum carries)
  ema_aligned   — price is above/below EMA(10) of last 10 bars at signal time

Sweeps: singles, pairs, triples anchored on best singles.
"""

import math, random
from itertools import combinations

NUM_DAYS     = 1000   # trading days per asset
BARS_PER_DAY = 78     # 9:30-16:00 = 78×5m
RANGE_BARS   = 3      # bars 0-2 = 9:30-9:45 opening range
MIN_OCC      = 50     # minimum signals to report

ASSETS = {
    "BTC": {"annual_vol": 0.85, "seed": 42,  "start": 65_000},
    "ETH": {"annual_vol": 0.95, "seed": 77,  "start": 3_500},
    "SOL": {"annual_vol": 1.10, "seed": 111, "start": 150},
}

CONFLUENCE_NAMES = [
    "vol_spike", "strong_close", "small_range", "early_break",
    "trend_into", "big_extension", "ema_aligned",
]

# ── bar generation ──────────────────────────────────────────────────────────
def synthetic_day(price, sigma, rng):
    bars = []
    for _ in range(BARS_PER_DAY):
        o = price
        body_ret = sigma * rng.gauss(0, 1)
        wick_ext = abs(sigma * rng.gauss(0, 1)) * 0.5
        c = o * math.exp(body_ret)
        h = max(o, c) * math.exp(wick_ext)
        l = min(o, c) * math.exp(-wick_ext)
        vol = abs(body_ret) / sigma * 1_000 * rng.uniform(0.5, 1.5)
        bars.append({"open": o, "high": h, "low": l, "close": c, "vol": vol})
        price = c
    return bars, price

# ── EMA helper ──────────────────────────────────────────────────────────────
def ema_at(bars, i, n):
    if i < n - 1:
        return None
    k = 2 / (n + 1)
    val = sum(bars[j]["close"] for j in range(i - n + 1, i + 1)) / n
    return val

# ── process one day, return first-breakout record ───────────────────────────
def process_day(day_bars, median_range):
    range_high = max(b["high"] for b in day_bars[:RANGE_BARS])
    range_low  = min(b["low"]  for b in day_bars[:RANGE_BARS])
    range_size = range_high - range_low
    if range_size <= 0:
        return None

    avg_range_vol = sum(b["vol"] for b in day_bars[:RANGE_BARS]) / RANGE_BARS

    for i in range(RANGE_BARS, BARS_PER_DAY - 1):
        bar = day_bars[i]
        nxt = day_bars[i + 1]

        bull = bar["close"] > range_high
        bear = bar["close"] < range_low
        if not bull and not bear:
            continue

        direction = "bull" if bull else "bear"
        win = (bull and nxt["close"] > nxt["open"]) or \
              (bear and nxt["close"] < nxt["open"])

        # ── confluences ─────────────────────────────────────────────────
        bar_range = bar["high"] - bar["low"]
        beyond = (bar["close"] - range_high) if bull else (range_low - bar["close"])

        # EMA of last 10 bars ending at bar i-1
        ema10 = ema_at(day_bars, i - 1, 10)

        prev3 = day_bars[max(0, i-3):i]
        bull_bars_prev = sum(1 for b in prev3 if b["close"] > b["open"])
        bear_bars_prev = sum(1 for b in prev3 if b["close"] < b["open"])

        conf = {
            "vol_spike":    bar["vol"] > avg_range_vol * 1.5,
            "strong_close": bar_range > 0 and (beyond / bar_range) > 0.30,
            "small_range":  range_size < median_range,
            "early_break":  i <= RANGE_BARS + 12,         # within 12 bars of range end
            "trend_into":   (bull and bull_bars_prev >= 2) or
                            (bear and bear_bars_prev >= 2),
            "big_extension": beyond > range_size * 0.25,
            "ema_aligned":  ema10 is not None and (
                            (bull and bar["close"] > ema10) or
                            (bear and bar["close"] < ema10)),
        }

        return {"win": win, "conf": conf, "direction": direction}

    return None   # no breakout today

# ── simulate all assets ─────────────────────────────────────────────────────
def simulate():
    all_records = []

    for asset, acfg in ASSETS.items():
        sigma = math.sqrt(5 / 525_600) * acfg["annual_vol"]
        rng   = random.Random(acfg["seed"])
        price = acfg["start"]

        # First pass: collect range sizes to compute median
        days = []
        for _ in range(NUM_DAYS):
            d, price = synthetic_day(price, sigma, rng)
            days.append(d)

        range_sizes = []
        for d in days:
            rs = max(b["high"] for b in d[:RANGE_BARS]) - min(b["low"] for b in d[:RANGE_BARS])
            range_sizes.append(rs)
        range_sizes.sort()
        median_range = range_sizes[len(range_sizes) // 2]

        for d in days:
            rec = process_day(d, median_range)
            if rec:
                all_records.append(rec)

    return all_records

# ── stats ────────────────────────────────────────────────────────────────────
def wr(records):
    if not records:
        return 0.0, 0
    return sum(r["win"] for r in records) / len(records) * 100, len(records)

def filter_conf(records, active_confs):
    return [r for r in records if all(r["conf"][c] for c in active_confs)]

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("ORB Confluence Search — 9:30-9:45 NY range, 5m signal")
    print(f"Target: next 5m bar COLOR  |  {NUM_DAYS} days × {len(ASSETS)} assets")
    print(f"First breakout of the day only  |  min {MIN_OCC} signals to report")
    print("=" * 68)

    records = simulate()
    base_wr, base_n = wr(records)
    sigs_per_day = base_n / NUM_DAYS   # per asset avg
    print(f"\n  Baseline (no filter): WR={base_wr:.1f}%  n={base_n:,}  "
          f"({sigs_per_day:.2f}/day per asset)\n")

    # ── Singles ─────────────────────────────────────────────────────────
    print(f"  {'─'*60}")
    print(f"  SINGLES")
    print(f"  {'─'*60}")
    print(f"  {'Confluence':<18}  {'WR%':>6}  {'n':>7}")
    singles_results = []
    for c in CONFLUENCE_NAMES:
        sub = filter_conf(records, [c])
        w, n = wr(sub)
        if n >= MIN_OCC:
            singles_results.append((c, w, n))
            print(f"  {c:<18}  {w:>6.1f}%  {n:>7,}")

    singles_results.sort(key=lambda x: -x[1])
    top5 = [s[0] for s in singles_results[:5]]

    # ── Pairs ────────────────────────────────────────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  PAIRS  (all combos)")
    print(f"  {'─'*60}")
    print(f"  {'Combo':<38}  {'WR%':>6}  {'n':>7}")
    pair_results = []
    for a, b in combinations(CONFLUENCE_NAMES, 2):
        sub = filter_conf(records, [a, b])
        w, n = wr(sub)
        if n >= MIN_OCC:
            pair_results.append(([a, b], w, n))

    pair_results.sort(key=lambda x: -x[1])
    for combo, w, n in pair_results[:15]:
        label = " + ".join(combo)
        print(f"  {label:<38}  {w:>6.1f}%  {n:>7,}")

    # ── Triples anchored on top-3 singles ────────────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  TRIPLES  (anchored on top-3 singles: {', '.join(top5[:3])})")
    print(f"  {'─'*60}")
    print(f"  {'Combo':<58}  {'WR%':>6}  {'n':>7}")
    triple_results = []
    for anchor in top5[:3]:
        others = [c for c in CONFLUENCE_NAMES if c != anchor]
        for a, b in combinations(others, 2):
            combo = [anchor, a, b]
            sub = filter_conf(records, combo)
            w, n = wr(sub)
            if n >= MIN_OCC:
                triple_results.append((combo, w, n))

    triple_results.sort(key=lambda x: -x[1])
    seen = set()
    for combo, w, n in triple_results:
        key = frozenset(combo)
        if key in seen:
            continue
        seen.add(key)
        label = " + ".join(combo)
        print(f"  {label:<58}  {w:>6.1f}%  {n:>7,}")
        if len(seen) >= 15:
            break

    # ── Sweet spot ───────────────────────────────────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  SWEET SPOT  (WR > 53%, n ≥ {MIN_OCC})")
    print(f"  {'─'*60}")
    print(f"  {'Combo':<58}  {'WR%':>6}  {'n':>7}")
    all_results = []
    for c in CONFLUENCE_NAMES:
        sub = filter_conf(records, [c])
        w, n = wr(sub)
        if n >= MIN_OCC:
            all_results.append(([c], w, n))
    for a, b in combinations(CONFLUENCE_NAMES, 2):
        sub = filter_conf(records, [a, b])
        w, n = wr(sub)
        if n >= MIN_OCC:
            all_results.append(([a, b], w, n))
    seen = set()
    for combo in triple_results:
        key = frozenset(combo[0])
        if key not in seen:
            seen.add(key)
            all_results.append(combo)

    all_results.sort(key=lambda x: -x[1])
    shown = 0
    for combo, w, n in all_results:
        if w > 53.0:
            label = " + ".join(combo)
            print(f"  {label:<58}  {w:>6.1f}%  {n:>7,}")
            shown += 1
    if shown == 0:
        print(f"  (nothing cleared 53% with n≥{MIN_OCC})")

    print()

if __name__ == "__main__":
    main()
