"""
15m C1/C2/C3 pattern with 4h structure filter — BTC + ETH + SOL

Filter rule:
  BEARISH → take the short only if C1 opens ABOVE the previous 4h candle's high
            (pattern is trading in premium territory above the 4h level)
  BULLISH → take the long  only if C1 opens BELOW the previous 4h candle's low
            (pattern is trading in discount territory below the 4h level)

Logic: the C1/C2/C3 trap pattern must be unfolding above (bear) or below (bull)
a key 4h structural level — not sweeping through it, but sitting above/below it.

Entry = C3 15m open  (Polymarket 50c or exchange market)
SL    = C2 high (bear) / C2 low (bull)
TP    = 2R from entry

Also sweeps additional filters on top of the HTF filter.

Usage:
  python htf_filter.py
  python htf_filter.py --real
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from itertools import combinations


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
MIN_OCC  = 40
R_MULT   = 2.0

BARS_PER_4H = 16   # 4h / 15m = 16 bars


# ── Assets ─────────────────────────────────────────────────────────────────────
def _sigma(minutes, annual_vol):
    return math.sqrt(minutes / 525_600) * annual_vol

ASSETS = {
    "BTC": {"annual_vol": 0.85, "symbol": "BTCUSDT",  "seed": 42,  "start_price": 65_000},
    "ETH": {"annual_vol": 0.95, "symbol": "ETHUSDT",  "seed": 77,  "start_price": 3_500},
    "SOL": {"annual_vol": 1.10, "symbol": "SOLUSDT",  "seed": 111, "start_price": 150},
}

EXTRA_SIGNALS = [
    "c1_qual_hi", "c2_body_1atr", "vol_spike_1_5x",
    "near_extreme", "atr_expanding", "trend_aligned",
]


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


# ── Aggregate 15m → 4h ────────────────────────────────────────────────────────
def to_4h(bars_15m):
    """Groups every 16 consecutive 15m bars into one 4h bar."""
    out = []
    n = len(bars_15m)
    i = 0
    while i + BARS_PER_4H <= n:
        chunk = bars_15m[i : i + BARS_PER_4H]
        out.append({
            "open":  chunk[0]["open"],
            "high":  max(b["high"] for b in chunk),
            "low":   min(b["low"]  for b in chunk),
            "close": chunk[-1]["close"],
        })
        i += BARS_PER_4H
    return out


# ── Indicators ─────────────────────────────────────────────────────────────────
def atr14(bars, i):
    if i < 14: return None
    s = 0
    for j in range(i - 13, i + 1):
        s += max(bars[j]["high"] - bars[j]["low"],
                 abs(bars[j]["high"] - bars[j-1]["close"]),
                 abs(bars[j]["low"]  - bars[j-1]["close"]))
    return s / 14

def avg_vol20(bars, i):
    if i < 20: return None
    return sum(bars[j]["vol"] for j in range(i - 19, i + 1)) / 20

def recent_high20(bars, i):
    if i < 20: return bars[i]["high"]
    return max(bars[j]["high"] for j in range(i - 19, i + 1))

def recent_low20(bars, i):
    if i < 20: return bars[i]["low"]
    return min(bars[j]["low"] for j in range(i - 19, i + 1))


# ── Simulate trade at 2R ───────────────────────────────────────────────────────
def sim_trade(c2, c3, direction):
    entry = c3["open"]
    stop  = c2["high"] if direction == "bear" else c2["low"]
    risk  = (stop - entry) if direction == "bear" else (entry - stop)
    if risk <= 0: return None
    target = entry - risk * R_MULT if direction == "bear" else entry + risk * R_MULT

    if direction == "bear":
        stop_hit   = c3["high"] >= stop
        target_hit = c3["low"]  <= target
    else:
        stop_hit   = c3["low"]  <= stop
        target_hit = c3["high"] >= target

    if stop_hit and not target_hit:  return -1.0
    if target_hit and not stop_hit:  return R_MULT
    if target_hit and stop_hit:      return -1.0
    pnl = (entry - c3["close"]) if direction == "bear" else (c3["close"] - entry)
    return pnl / risk


# ── Build records ──────────────────────────────────────────────────────────────
def build_records(bars_15m, bars_4h):
    records = []
    n = len(bars_15m)

    for i in range(2, n - 1):
        c1_idx = i - 2
        c2_idx = i - 1
        c1, c2, c3 = bars_15m[c1_idx], bars_15m[c2_idx], bars_15m[i + 1]

        # Which 4h candle does C2 belong to, and what is the PREVIOUS 4h?
        c2_4h_idx = c2_idx // BARS_PER_4H
        prev_4h_idx = c2_4h_idx - 1
        if prev_4h_idx < 0 or prev_4h_idx >= len(bars_4h):
            continue
        prev_4h = bars_4h[prev_4h_idx]

        c1_body  = abs(c1["close"] - c1["open"])
        c1_range = c1["high"] - c1["low"] or 1e-9
        c2_body  = abs(c2["close"] - c2["open"])
        c2_range = c2["high"] - c2["low"] or 1e-9

        a14   = atr14(bars_15m, c2_idx)   or 1e-9
        a14_5 = atr14(bars_15m, c2_idx-5) if c2_idx >= 5 else None
        av20  = avg_vol20(bars_15m, c2_idx) or 1e-9

        for direction in ("bear", "bull"):
            bear = direction == "bear"

            c1_green = c1["close"] > c1["open"]
            c1_red   = c1["close"] < c1["open"]
            c2_red   = c2["close"] < c2["open"]
            c2_green = c2["close"] > c2["open"]

            # Base 15m pattern
            if bear:
                if not c1_green or c2["high"] < c1["high"] or not c2_red: continue
            else:
                if not c1_red  or c2["low"]  > c1["low"]  or not c2_green: continue

            # ── HTF FILTER ────────────────────────────────────────────────────
            # Bear: C1 must OPEN above the previous 4h high (setup in premium)
            # Bull: C1 must OPEN below the previous 4h low  (setup in discount)
            if bear:
                htf_ok = c1["open"] > prev_4h["high"]
            else:
                htf_ok = c1["open"] < prev_4h["low"]

            r = sim_trade(c2, c3, direction)
            if r is None: continue

            r_high = recent_high20(bars_15m, c2_idx)
            r_low  = recent_low20(bars_15m,  c2_idx)

            trend_red   = sum(1 for j in range(max(0, c1_idx-3), c1_idx)
                              if bars_15m[j]["close"] < bars_15m[j]["open"])
            trend_green = sum(1 for j in range(max(0, c1_idx-3), c1_idx)
                              if bars_15m[j]["close"] > bars_15m[j]["open"])

            extra = {
                "c1_qual_hi":     c1_body / c1_range > 0.55,
                "c2_body_1atr":   (c2_body / a14) > 1.0,
                "vol_spike_1_5x": c2["vol"] / av20 > 1.5,
                "near_extreme":   (bear and abs(c2["high"] - r_high) / r_high < 0.003) or
                                  (not bear and abs(c2["low"] - r_low) / r_low < 0.003),
                "atr_expanding":  (a14_5 is not None) and (a14 > a14_5),
                "trend_aligned":  (bear and trend_green >= 2) or
                                  (not bear and trend_red >= 2),
            }

            records.append({
                "htf_ok": htf_ok,
                "extra":  extra,
                "r":      r,
                "direction": direction,
                "c1_open": c1["open"],
                "prev_4h_high": prev_4h["high"], "prev_4h_low": prev_4h["low"],
            })

    return records


# ── Analyse ────────────────────────────────────────────────────────────────────
def analyse(records, calendar_days):
    n_all = len(records)
    if n_all == 0:
        print("  No records."); return

    def stats(recs, label, spd_days):
        if len(recs) < MIN_OCC: return
        wins = sum(1 for r in recs if r["r"] > 0)
        ev   = sum(r["r"] for r in recs) / len(recs)
        wr   = wins / len(recs) * 100
        spd  = len(recs) / spd_days
        print(f"  {label:<42}  WR={wr:>5.1f}%  EV={ev:>+7.4f}R  "
              f"{spd:>5.2f}/day  n={len(recs):,}")

    b_wins = sum(1 for r in records if r["r"] > 0)
    b_ev   = sum(r["r"] for r in records) / n_all
    b_wr   = b_wins / n_all * 100
    print(f"\n  Baseline (pattern only, no HTF): WR={b_wr:.1f}%  EV={b_ev:+.4f}R  "
          f"{n_all/calendar_days:.1f}/day  n={n_all:,}")

    htf = [r for r in records if r["htf_ok"]]
    no_htf = [r for r in records if not r["htf_ok"]]

    print(f"\n  ─── HTF FILTER COMPARISON ───────────────────────────────────────")
    stats(htf,    "C1 opens beyond 4h level (HTF filter ON)", calendar_days)
    stats(no_htf, "C1 inside 4h range      (HTF filter OFF)", calendar_days)

    # Direction breakdown
    htf_bear = [r for r in htf if r["direction"] == "bear"]
    htf_bull = [r for r in htf if r["direction"] == "bull"]
    print(f"\n  ─── HTF FILTER: DIRECTION BREAKDOWN ─────────────────────────────")
    stats(htf_bear, "Bearish (C1 above 4h high → short)", calendar_days)
    stats(htf_bull, "Bullish (C1 below 4h low  → long)", calendar_days)

    # Extra signals stacked on HTF
    print(f"\n  ─── HTF + EXTRA SIGNAL COMBOS (sorted by EV) ───────────────────")
    print(f"  {'Filter combo':<42}  {'WR%':>6}  {'EV/trade':>9}  {'Sig/day':>8}  {'n':>6}")
    print("  " + "-"*76)

    rows = []
    # Singles
    for sig in EXTRA_SIGNALS:
        recs = [r for r in htf if r["extra"][sig]]
        if len(recs) < MIN_OCC: continue
        wins = sum(1 for r in recs if r["r"] > 0)
        ev   = sum(r["r"] for r in recs) / len(recs)
        rows.append({"label": f"HTF + {sig}", "wr": wins/len(recs)*100,
                     "ev": ev, "n": len(recs), "spd": len(recs)/calendar_days})

    # Pairs
    for s1, s2 in combinations(EXTRA_SIGNALS, 2):
        recs = [r for r in htf if r["extra"][s1] and r["extra"][s2]]
        if len(recs) < MIN_OCC: continue
        wins = sum(1 for r in recs if r["r"] > 0)
        ev   = sum(r["r"] for r in recs) / len(recs)
        rows.append({"label": f"HTF + {s1} + {s2}", "wr": wins/len(recs)*100,
                     "ev": ev, "n": len(recs), "spd": len(recs)/calendar_days})

    rows.sort(key=lambda r: r["ev"], reverse=True)
    for row in rows[:20]:
        print(f"  {row['label']:<42}  {row['wr']:>6.1f}  {row['ev']:>+9.4f}  "
              f"{row['spd']:>8.2f}  {row['n']:>6,}")

    # Sweet spot
    sweet = sorted([r for r in rows if r["ev"] > 0 and 0.3 <= r["spd"] <= 5.0],
                   key=lambda r: r["ev"], reverse=True)
    if sweet:
        print(f"\n  ─── SWEET SPOT (0.3–5 sig/day, EV>0) ──────────────────────────")
        print(f"  {'Filter combo':<42}  {'WR%':>6}  {'EV/trade':>9}  {'Sig/day':>8}  {'n':>6}")
        print("  " + "-"*76)
        for row in sweet[:10]:
            print(f"  {row['label']:<42}  {row['wr']:>6.1f}  {row['ev']:>+9.4f}  "
                  f"{row['spd']:>8.2f}  {row['n']:>6,}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    print(f"15m C1/C2/C3 + 4h Structure Filter  |  BTC + ETH + SOL  |  {mode}")
    print(f"Bear: C1 opens ABOVE prev 4h high (premium zone) → short")
    print(f"Bull: C1 opens BELOW prev 4h low  (discount zone) → long")
    print(f"TP={R_MULT}R  SL=C2 extreme\n")

    sim_bars  = 40_000          # 15m bars
    real_days = 365
    bpd       = 96              # 15m bars per day
    calendar_days = sim_bars / bpd

    all_records = []
    print(f"  {calendar_days:.0f} calendar days per asset  ({sim_bars:,} 15m bars)")

    for asset, acfg in ASSETS.items():
        sigma = _sigma(15, acfg["annual_vol"])
        bars  = None

        if USE_REAL:
            try:
                bars = fetch_binance(acfg["symbol"], "15m", real_days)
                print(f"    [{asset}] real data: {len(bars):,} bars")
            except Exception as e:
                print(f"    [{asset}] Binance error — synthetic.")

        if bars is None:
            bars = synthetic(sim_bars, sigma, acfg["seed"], acfg["start_price"])
            print(f"    [{asset}] synthetic {len(bars):,} bars  σ/bar={sigma:.5f}")

        bars_4h = to_4h(bars)
        print(f"    [{asset}] aggregated to {len(bars_4h)} 4h bars")

        recs = build_records(bars, bars_4h)
        all_records.extend(recs)
        print(f"    [{asset}] {len(recs):,} setups found")

    print(f"\n  Combined: {len(all_records):,} setups across 3 assets")
    print("=" * 80)

    analyse(all_records, calendar_days)

    # Show a few example trades
    htf_trades = [r for r in all_records if r["htf_ok"]]
    if htf_trades:
        print(f"\n  ─── SAMPLE TRADES (HTF filter hit) ─────────────────────────────")
        for ex in htf_trades[:5]:
            d = ex["direction"].upper()
            outcome = "WIN ✓" if ex["r"] > 0 else "LOSS ✗"
            if d == "BEAR":
                print(f"  {d}  C1 open={ex['c1_open']:,.2f} > 4h high={ex['prev_4h_high']:,.2f}  "
                      f"→ short  {outcome}  ({ex['r']:+.2f}R)")
            else:
                print(f"  {d}  C1 open={ex['c1_open']:,.2f} < 4h low={ex['prev_4h_low']:,.2f}  "
                      f"→ long   {outcome}  ({ex['r']:+.2f}R)")

    print()


if __name__ == "__main__":
    main()
