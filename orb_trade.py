"""
ORB Trade — 9:30-9:45 NY range, baseline only (no filters)

Entry  : close of the breakout 5m candle
SL     : opposite side of the range
         bull break → SL = range_low
         bear break → SL = range_high
TP     : tested at multiple R:R multiples
Conservative sim: if SL and TP both touched within same bar, SL is hit first.
If neither hit by end of day, exit at day close (open P&L).

First breakout of the day only.
"""

import math, random

NUM_DAYS     = 2000
BARS_PER_DAY = 78
RANGE_BARS   = 3

TP_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

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
        bars.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    return bars, price

# ── simulate one trade ───────────────────────────────────────────────────────
def sim_trade(day_bars, signal_idx, entry, sl, tp_mult, bull):
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    tp = entry + risk * tp_mult if bull else entry - risk * tp_mult

    for j in range(signal_idx + 1, BARS_PER_DAY):
        bar = day_bars[j]
        if bull:
            if bar["low"] <= sl:    return -1.0          # SL hit
            if bar["high"] >= tp:   return tp_mult        # TP hit
        else:
            if bar["high"] >= sl:   return -1.0          # SL hit
            if bar["low"] <= tp:    return tp_mult        # TP hit

    # End of day — exit at close
    eod = day_bars[-1]["close"]
    return (eod - entry) / risk if bull else (entry - eod) / risk

# ── process one day ──────────────────────────────────────────────────────────
def process_day(day_bars):
    rh = max(b["high"] for b in day_bars[:RANGE_BARS])
    rl = min(b["low"]  for b in day_bars[:RANGE_BARS])
    if rh <= rl:
        return None

    for i in range(RANGE_BARS, BARS_PER_DAY - 1):
        bar = day_bars[i]
        bull = bar["close"] > rh
        bear = bar["close"] < rl
        if not bull and not bear:
            continue

        entry = bar["close"]
        sl    = rl if bull else rh

        results = {}
        for tp in TP_MULTS:
            r = sim_trade(day_bars, i, entry, sl, tp, bull)
            results[tp] = r

        return {"bull": bull, "results": results}

    return None

# ── simulate all assets ──────────────────────────────────────────────────────
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

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("ORB Trade — 9:30-9:45 NY range  |  baseline, no filters")
    print(f"Entry = breakout bar close  |  SL = opposite range side")
    print(f"{NUM_DAYS} days × {len(ASSETS)} assets  |  first breakout of day only")
    print("=" * 62)

    records = simulate()
    n_total = len(records)
    sigs_per_day = n_total / NUM_DAYS
    print(f"\n  Total signals: {n_total:,}  ({sigs_per_day:.2f}/day per asset)\n")

    print(f"  {'TP':<8}  {'WR%':>6}  {'EV/trade':>10}  {'Total EV':>10}  {'Best?'}")
    print("  " + "-" * 52)

    best_ev   = -999
    best_mult = None
    results_table = []

    for tp in TP_MULTS:
        valid = [r for r in records if r["results"][tp] is not None]
        if not valid:
            continue
        wins = sum(1 for r in valid if r["results"][tp] > 0)
        wr   = wins / len(valid) * 100
        ev   = sum(r["results"][tp] for r in valid) / len(valid)
        total_ev = ev * len(valid)
        results_table.append((tp, wr, ev, total_ev, len(valid)))
        if ev > best_ev:
            best_ev   = ev
            best_mult = tp

    for tp, wr, ev, total_ev, n in results_table:
        marker = "  ◄ BEST" if tp == best_mult else ""
        print(f"  TP={tp}R    {wr:>6.1f}%   {ev:>+10.4f}R   {total_ev:>+10.1f}R{marker}")

    print(f"\n  Direction breakdown (best TP={best_mult}R):")
    bull_recs = [r for r in records if r["bull"]  and r["results"][best_mult] is not None]
    bear_recs = [r for r in records if not r["bull"] and r["results"][best_mult] is not None]
    for label, recs in [("Bull breaks", bull_recs), ("Bear breaks", bear_recs)]:
        if not recs: continue
        wr  = sum(1 for r in recs if r["results"][best_mult] > 0) / len(recs) * 100
        ev  = sum(r["results"][best_mult] for r in recs) / len(recs)
        print(f"  {label:<14}: WR={wr:.1f}%  EV={ev:+.4f}R  n={len(recs):,}")

    print()

if __name__ == "__main__":
    main()
