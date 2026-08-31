"""
ORB Final — 9:30-9:45 NY range, 5m signal, color prediction

Filters:
  ema_aligned   — price above/below 10-bar EMA at breakout (trending in right direction)
  small_range   — opening range tighter than median day range (compression)
  early_break   — breakout within 12 bars of 9:45 (before ~10:30 AM)
  atr_expanding — 14-bar ATR at signal bar is larger than 5 bars ago (expanding vol)

Win = next 5m bar closes in the breakout direction (green if bull break, red if bear break).
No SL / TP — pure color guess.
"""

import math, random

NUM_DAYS     = 1000
BARS_PER_DAY = 78    # 9:30-16:00
RANGE_BARS   = 3     # 9:30-9:45

ASSETS = {
    "BTC": {"annual_vol": 0.85, "seed": 42,  "start": 65_000},
    "ETH": {"annual_vol": 0.95, "seed": 77,  "start": 3_500},
    "SOL": {"annual_vol": 1.10, "seed": 111, "start": 150},
}

# ── bar generation ───────────────────────────────────────────────────────────
def synthetic_day(price, sigma, rng):
    bars = []
    for _ in range(BARS_PER_DAY):
        o = price
        body = sigma * rng.gauss(0, 1)
        wick = abs(sigma * rng.gauss(0, 1)) * 0.5
        c = o * math.exp(body)
        h = max(o, c) * math.exp(wick)
        l = min(o, c) * math.exp(-wick)
        vol = abs(body) / sigma * 1_000 * rng.uniform(0.5, 1.5)
        bars.append({"open": o, "high": h, "low": l, "close": c, "vol": vol})
        price = c
    return bars, price

# ── ATR(n) at bar i ──────────────────────────────────────────────────────────
def atr_n(bars, i, n=5):
    if i < n:
        return None
    trs = [max(bars[j]["high"] - bars[j]["low"],
               abs(bars[j]["high"] - bars[j-1]["close"]),
               abs(bars[j]["low"]  - bars[j-1]["close"]))
           for j in range(i - n + 1, i + 1)]
    return sum(trs) / n

# ── EMA(10) at bar i ─────────────────────────────────────────────────────────
def ema10(bars, i):
    n = 10
    if i < n - 1:
        return None
    k = 2 / (n + 1)
    val = sum(bars[j]["close"] for j in range(i - n + 1, i + 1)) / n
    return val

# ── process one day ──────────────────────────────────────────────────────────
def process_day(day_bars, median_range):
    rh = max(b["high"] for b in day_bars[:RANGE_BARS])
    rl = min(b["low"]  for b in day_bars[:RANGE_BARS])
    rs = rh - rl
    if rs <= 0:
        return None

    for i in range(RANGE_BARS, BARS_PER_DAY - 1):
        bar = day_bars[i]
        nxt = day_bars[i + 1]

        bull = bar["close"] > rh
        bear = bar["close"] < rl
        if not bull and not bear:
            continue

        win = (bull and nxt["close"] > nxt["open"]) or \
              (bear and nxt["close"] < nxt["open"])

        e = ema10(day_bars, i - 1)
        # ATR(5): compare last 5 bars vs 5 bars before that — works as early as bar 8
        a_now  = atr_n(day_bars, i,     n=5)
        a_prev = atr_n(day_bars, i - 5, n=5) if i >= 10 else None

        return {
            "win":          win,
            "ema_aligned":  e is not None and (
                            (bull and bar["close"] > e) or
                            (bear and bar["close"] < e)),
            "small_range":  rs < median_range,
            "early_break":  i <= RANGE_BARS + 12,
            "atr_expanding": a_now is not None and a_prev is not None and a_now > a_prev,
        }

    return None

# ── simulate all assets ──────────────────────────────────────────────────────
def simulate():
    all_records = []
    for asset, acfg in ASSETS.items():
        sigma = math.sqrt(5 / 525_600) * acfg["annual_vol"]
        rng   = random.Random(acfg["seed"])
        price = acfg["start"]
        days  = []
        for _ in range(NUM_DAYS):
            d, price = synthetic_day(price, sigma, rng)
            days.append(d)

        sizes = sorted(max(b["high"] for b in d[:RANGE_BARS]) -
                       min(b["low"]  for b in d[:RANGE_BARS]) for d in days)
        median_range = sizes[len(sizes) // 2]

        for d in days:
            r = process_day(d, median_range)
            if r:
                all_records.append(r)
    return all_records

# ── stats ────────────────────────────────────────────────────────────────────
def wr(records):
    if not records:
        return 0.0, 0
    return sum(r["win"] for r in records) / len(records) * 100, len(records)

def filt(records, *keys):
    return [r for r in records if all(r[k] for k in keys)]

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("ORB — 9:30-9:45 NY range, 5m signal, color prediction")
    print(f"{NUM_DAYS} days × {len(ASSETS)} assets  |  first breakout of day only")
    print("=" * 56)

    records = simulate()

    layers = [
        ("Baseline (no filter)",                    []),
        ("+ ema_aligned",                           ["ema_aligned"]),
        ("+ small_range",                           ["small_range"]),
        ("+ early_break",                           ["early_break"]),
        ("+ atr_expanding",                         ["atr_expanding"]),
        ("ema + small_range",                       ["ema_aligned", "small_range"]),
        ("ema + early_break",                       ["ema_aligned", "early_break"]),
        ("ema + atr",                               ["ema_aligned", "atr_expanding"]),
        ("small_range + early_break",               ["small_range", "early_break"]),
        ("small_range + atr",                       ["small_range", "atr_expanding"]),
        ("early_break + atr",                       ["early_break", "atr_expanding"]),
        ("ema + small_range + early_break",         ["ema_aligned", "small_range", "early_break"]),
        ("ema + small_range + atr",                 ["ema_aligned", "small_range", "atr_expanding"]),
        ("ema + early_break + atr",                 ["ema_aligned", "early_break", "atr_expanding"]),
        ("small_range + early_break + atr",         ["small_range", "early_break", "atr_expanding"]),
        ("ALL FOUR",                                ["ema_aligned", "small_range", "early_break", "atr_expanding"]),
    ]

    print(f"\n  {'Setup':<38}  {'WR%':>6}  {'n':>6}")
    print("  " + "-" * 54)
    for label, keys in layers:
        sub = filt(records, *keys)
        w, n = wr(sub)
        flag = "  ◄" if w > 55.0 and n >= 30 else ""
        print(f"  {label:<38}  {w:>6.1f}%  {n:>6,}{flag}")

    print()

if __name__ == "__main__":
    main()
