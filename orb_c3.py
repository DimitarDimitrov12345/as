"""
ORB C1/C2/C3 momentum — 9:30-9:45 NY range

C1 = first candle to close outside the opening range (the breakout)
C2 = candle immediately after C1 — must close in same direction as C1
C3 = candle after C2 — WR that it also closes in the same direction

Pure color prediction, no SL/TP.
Also shows what happens when C2 goes AGAINST C1 (for comparison).
"""

import math, random

NUM_DAYS     = 2000
BARS_PER_DAY = 78
RANGE_BARS   = 3

ASSETS = {
    "BTC": {"annual_vol": 0.85, "seed": 42,  "start": 65_000},
    "ETH": {"annual_vol": 0.95, "seed": 77,  "start": 3_500},
    "SOL": {"annual_vol": 1.10, "seed": 111, "start": 150},
}

def synthetic_day(price, sigma, rng):
    bars = []
    for _ in range(BARS_PER_DAY):
        o = price
        body = sigma * rng.gauss(0, 1)
        wick = abs(sigma * rng.gauss(0, 1)) * 0.5
        c = o * math.exp(body)
        h = max(o, c) * math.exp(wick)
        l = min(o, c) * math.exp(-wick)
        bars.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    return bars, price

def process_day(day_bars):
    rh = max(b["high"] for b in day_bars[:RANGE_BARS])
    rl = min(b["low"]  for b in day_bars[:RANGE_BARS])
    if rh <= rl:
        return None

    for i in range(RANGE_BARS, BARS_PER_DAY - 2):
        c1 = day_bars[i]
        bull = c1["close"] > rh
        bear = c1["close"] < rl
        if not bull and not bear:
            continue

        c2 = day_bars[i + 1]
        c3 = day_bars[i + 2]

        c2_same = (bull and c2["close"] > c2["open"]) or \
                  (bear and c2["close"] < c2["open"])

        c3_same = (bull and c3["close"] > c3["open"]) or \
                  (bear and c3["close"] < c3["open"])

        return {
            "bull":    bull,
            "c2_same": c2_same,
            "c3_same": c3_same,
        }

    return None

def simulate():
    all_records = []
    for asset, acfg in ASSETS.items():
        sigma = math.sqrt(5 / 525_600) * acfg["annual_vol"]
        rng   = random.Random(acfg["seed"])
        price = acfg["start"]
        for _ in range(NUM_DAYS):
            d, price = synthetic_day(price, sigma, rng)
            r = process_day(d)
            if r:
                all_records.append(r)
    return all_records

def wr(records, key):
    if not records: return 0.0, 0
    return sum(r[key] for r in records) / len(records) * 100, len(records)

def main():
    print("ORB C1/C2/C3 — 9:30-9:45 NY range  |  first breakout of day")
    print(f"{NUM_DAYS} days × {len(ASSETS)} assets")
    print("=" * 58)

    records = simulate()

    # ── C2 continuation ─────────────────────────────────────────
    c2_yes = [r for r in records if r["c2_same"]]
    c2_no  = [r for r in records if not r["c2_same"]]

    w, n = wr(records, "c2_same")
    print(f"\n  C1 breaks range — how often does C2 continue?")
    print(f"  C2 same direction as C1 : {w:.1f}%  (n={n:,})")

    # ── C3 given C2 continued ───────────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  C3 direction given C2 also continued (C2 same ✓)")
    print(f"  {'─'*50}")
    w3, n3 = wr(c2_yes, "c3_same")
    print(f"  C3 same direction        : {w3:.1f}%  (n={n3:,})")
    print(f"  C3 opposite direction    : {100-w3:.1f}%")

    # breakdown by bull/bear
    c2y_bull = [r for r in c2_yes if r["bull"]]
    c2y_bear = [r for r in c2_yes if not r["bull"]]
    wb, nb = wr(c2y_bull, "c3_same")
    ws, ns = wr(c2y_bear, "c3_same")
    print(f"\n  Bull breakout (C1+C2 green) → C3 green: {wb:.1f}%  n={nb:,}")
    print(f"  Bear breakout (C1+C2 red)   → C3 red  : {ws:.1f}%  n={ns:,}")

    # ── C3 given C2 went AGAINST ───────────────────────────────
    print(f"\n  {'─'*50}")
    print(f"  C3 direction when C2 went AGAINST C1 (C2 same ✗)")
    print(f"  {'─'*50}")
    w3n, n3n = wr(c2_no, "c3_same")
    print(f"  C3 same as C1 direction  : {w3n:.1f}%  (n={n3n:,})")
    print(f"  (i.e. C3 reverses back with C1 after C2 pullback)")

    print()

if __name__ == "__main__":
    main()
