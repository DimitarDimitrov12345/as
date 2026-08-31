"""
Opening Range Breakout (ORB) — NY 9:30-9:45 range on 5m bars

Range candle : 9:30-9:45 NY time = first 3 consecutive 5m bars of NY session
               range_high = max of those 3 highs
               range_low  = min of those 3 lows

Signal : A 5m bar (after 9:45) closes ABOVE range_high  → bullish break
         A 5m bar (after 9:45) closes BELOW range_low   → bearish break

Trade  : Enter at open of NEXT 5m bar after the signal bar
Win    : That next bar closes in the breakout direction
         (bull break → next bar close > open; bear break → next bar close < open)

Also tests with a TP/SL:
  SL = opposite side of range (range_low for bull, range_high for bear)
  TP = 1R, 1.5R, 2R
"""

import math, random, sys
from itertools import combinations

ANNUAL_VOL  = 0.85   # BTC
SIGMA_5M    = math.sqrt(5 / 525_600) * ANNUAL_VOL
BARS_PER_DAY = 78    # 9:30 AM – 4:00 PM = 390 min = 78 × 5m bars
RANGE_BARS  = 3      # bars 0-2 = 9:30, 9:35, 9:40  (the 9:30-9:45 15m candle)
NUM_DAYS    = 500    # trading days to simulate per asset
TP_MULTS    = [1.0, 1.5, 2.0]

ASSETS = {
    "BTC": {"annual_vol": 0.85, "seed": 42,  "start_price": 65_000},
    "ETH": {"annual_vol": 0.95, "seed": 77,  "start_price": 3_500},
    "SOL": {"annual_vol": 1.10, "seed": 111, "start_price": 150},
}

# ── synthetic bar generation ────────────────────────────────────────────────
def synthetic_day(price, sigma, rng):
    """Generate one trading day of 5m bars, return (bars, end_price)."""
    bars = []
    for _ in range(BARS_PER_DAY):
        o = price
        r1, r2 = rng.gauss(0, 1), rng.gauss(0, 1)
        body_ret = sigma * r1
        wick_ext = abs(sigma * r2) * 0.5
        c = o * math.exp(body_ret)
        h = max(o, c) * math.exp(wick_ext)
        l = min(o, c) * math.exp(-wick_ext)
        vol = abs(body_ret) / sigma * 1_000 * rng.uniform(0.5, 1.5)
        bars.append({"open": o, "high": h, "low": l, "close": c, "vol": vol})
        price = c
    return bars, price

# ── simulate all days for one asset ────────────────────────────────────────
def simulate_asset(annual_vol, seed, start_price):
    sigma = math.sqrt(5 / 525_600) * annual_vol
    rng   = random.Random(seed)
    price = start_price
    all_days = []
    for _ in range(NUM_DAYS):
        day_bars, price = synthetic_day(price, sigma, rng)
        all_days.append(day_bars)
    return all_days

# ── trade logic for one day ─────────────────────────────────────────────────
def process_day(day_bars, only_first=False):
    """
    Returns list of records: {direction, next_green, r_at_tp} for each signal.
    only_first: if True, stop after the first breakout signal.
    """
    # Range from bars 0, 1, 2
    range_high = max(b["high"]  for b in day_bars[:RANGE_BARS])
    range_low  = min(b["low"]   for b in day_bars[:RANGE_BARS])
    range_size = range_high - range_low
    if range_size <= 0:
        return []

    records = []
    for i in range(RANGE_BARS, BARS_PER_DAY - 1):
        bar  = day_bars[i]
        nxt  = day_bars[i + 1]

        bull_break = bar["close"] > range_high
        bear_break = bar["close"] < range_low
        if not bull_break and not bear_break:
            continue

        direction   = "bull" if bull_break else "bear"
        entry       = nxt["open"]
        next_green  = nxt["close"] > nxt["open"]   # raw direction of next bar
        next_red    = nxt["close"] < nxt["open"]

        win_dir = (direction == "bull" and next_green) or \
                  (direction == "bear" and next_red)

        # R:R simulation — SL at opposite range boundary
        if direction == "bull":
            sl   = range_low
            risk = entry - sl
        else:
            sl   = range_high
            risk = sl - entry

        if risk <= 0:
            continue

        rr_results = {}
        for tp_mult in TP_MULTS:
            tp = entry + risk * tp_mult if direction == "bull" else entry - risk * tp_mult
            # Conservative: check if SL hit before TP within next bar
            if direction == "bull":
                if nxt["low"] <= sl:
                    rr_results[tp_mult] = -1.0   # SL hit first (conservative)
                elif nxt["high"] >= tp:
                    rr_results[tp_mult] = tp_mult
                else:
                    rr_results[tp_mult] = (nxt["close"] - entry) / risk
            else:
                if nxt["high"] >= sl:
                    rr_results[tp_mult] = -1.0
                elif nxt["low"] <= tp:
                    rr_results[tp_mult] = tp_mult
                else:
                    rr_results[tp_mult] = (entry - nxt["close"]) / risk

        records.append({
            "direction": direction,
            "win_dir":   win_dir,
            "rr":        rr_results,
        })

        if only_first:
            break

    return records

# ── stats helpers ────────────────────────────────────────────────────────────
def wr_ev(records, tp_mult):
    if not records: return None, None, None
    wins = sum(1 for r in records if r["rr"][tp_mult] > 0)
    ev   = sum(r["rr"][tp_mult] for r in records) / len(records)
    return wins / len(records) * 100, ev, len(records)

def dir_wr(records):
    if not records: return 0.0
    return sum(1 for r in records if r["win_dir"]) / len(records) * 100

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("Opening Range Breakout — 9:30-9:45 NY range, 5m signal + 5m target")
    print(f"Range = first {RANGE_BARS}×5m bars (9:30-9:45)  |  {NUM_DAYS} trading days per asset")
    print(f"Signal: 5m bar closes outside range  →  predict next 5m bar direction")
    print("=" * 72)

    for mode_label, only_first in [("ALL breakouts per day", False),
                                   ("FIRST breakout only",  True)]:
        print(f"\n{'─'*72}")
        print(f"  MODE: {mode_label}")
        print(f"{'─'*72}")

        all_records  = []
        bull_records = []
        bear_records = []

        for asset, acfg in ASSETS.items():
            days = simulate_asset(acfg["annual_vol"], acfg["seed"], acfg["start_price"])
            asset_recs = []
            for day in days:
                asset_recs.extend(process_day(day, only_first=only_first))

            n_bull = sum(1 for r in asset_recs if r["direction"] == "bull")
            n_bear = sum(1 for r in asset_recs if r["direction"] == "bear")
            print(f"\n  [{asset}]  {len(asset_recs)} signals  "
                  f"({n_bull} bull / {n_bear} bear)  "
                  f"over {NUM_DAYS} days")
            all_records.extend(asset_recs)
            bull_records.extend(r for r in asset_recs if r["direction"] == "bull")
            bear_records.extend(r for r in asset_recs if r["direction"] == "bear")

        sigs_per_day = len(all_records) / (NUM_DAYS * len(ASSETS))
        print(f"\n  Combined: {len(all_records):,} signals  "
              f"({sigs_per_day:.2f}/day avg across {len(ASSETS)} assets)")

        # ── Pure direction WR (no TP/SL) ──────────────────────────────────
        print(f"\n  {'─'*60}")
        print(f"  DIRECTION WR  (win = next 5m bar closes in breakout direction)")
        print(f"  {'─'*60}")
        print(f"  All directions : {dir_wr(all_records):.1f}%  n={len(all_records):,}")
        print(f"  Bull breaks    : {dir_wr(bull_records):.1f}%  n={len(bull_records):,}")
        print(f"  Bear breaks    : {dir_wr(bear_records):.1f}%  n={len(bear_records):,}")

        # ── R:R table ─────────────────────────────────────────────────────
        print(f"\n  {'─'*60}")
        print(f"  R:R TABLE  (SL = opposite range boundary)")
        print(f"  {'─'*60}")
        print(f"  {'TP mult':<10}  {'WR%':>6}  {'EV/trade':>10}  {'n':>8}")
        for tp in TP_MULTS:
            for label, recs in [("All  ", all_records),
                                 ("Bull ", bull_records),
                                 ("Bear ", bear_records)]:
                w, ev, n = wr_ev(recs, tp)
                if w is None: continue
                print(f"  {label} TP={tp}R   {w:>6.1f}%   {ev:>+10.4f}R   {n:>8,}")
            print()

    print()

if __name__ == "__main__":
    main()
