"""
EMA-filtered C1/C2/C3 pattern — BTC + ETH + SOL

Rule:
  Bearish setup  → only take if C3 opens BELOW the EMA  (downtrend)
  Bullish setup  → only take if C3 opens ABOVE the EMA  (uptrend)

Entry = C3 open (Polymarket 50c or exchange market order)
SL    = C2 high (bear) / C2 low (bull)
TP    = 2R from entry (best EV from prior backtests)

Tests EMA 20 / 50 / 100 / 200 on 30m and 1h across all 3 assets.
Also shows best signal combos on top of the EMA filter.

Usage:
  python ema_filter.py
  python ema_filter.py --real
"""

import urllib.request, urllib.parse, json, time
import random, math, sys
from datetime import datetime, timedelta
from itertools import combinations


# ── CLI ────────────────────────────────────────────────────────────────────────
args     = sys.argv[1:]
USE_REAL = "--real" in args
MIN_OCC  = 40
R_MULT   = 2.0   # TP at 2R


# ── Assets & timeframes ────────────────────────────────────────────────────────
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
}

EMA_LENGTHS = [20, 50, 100, 200]

# Extra signals to sweep on top of EMA filter
EXTRA_SIGNALS = [
    "vol_spike_1_5x", "near_extreme", "c1_qual_hi",
    "atr_expanding", "c2_body_1atr", "trend_aligned",
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


# ── Indicators ─────────────────────────────────────────────────────────────────
def compute_emas(bars, lengths):
    """Returns dict[length -> list of EMA values], one per bar (None until warm)."""
    result = {n: [None] * len(bars) for n in lengths}
    for n in lengths:
        k = 2 / (n + 1)
        val = None
        for i, b in enumerate(bars):
            if i < n - 1:
                result[n][i] = None
            elif i == n - 1:
                val = sum(bars[j]["close"] for j in range(n)) / n
                result[n][i] = val
            else:
                val = b["close"] * k + val * (1 - k)
                result[n][i] = val
    return result

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


# ── Simulate one trade at 2R ───────────────────────────────────────────────────
def sim_trade(c2, c3, direction):
    """Returns R gained. None = invalid (entry past stop)."""
    entry = c3["open"]
    stop  = c2["high"] if direction == "bear" else c2["low"]
    risk  = (stop - entry) if direction == "bear" else (entry - stop)
    if risk <= 0:
        return None
    target = entry - risk * R_MULT if direction == "bear" else entry + risk * R_MULT

    if direction == "bear":
        stop_hit   = c3["high"] >= stop
        target_hit = c3["low"]  <= target
    else:
        stop_hit   = c3["low"]  <= stop
        target_hit = c3["high"] >= target

    if stop_hit and not target_hit:  return -1.0
    if target_hit and not stop_hit:  return R_MULT
    if target_hit and stop_hit:      return -1.0   # conservative
    pnl = (entry - c3["close"]) if direction == "bear" else (c3["close"] - entry)
    return pnl / risk


# ── Build records ──────────────────────────────────────────────────────────────
def build_records(bars, emas_dict):
    """
    Returns list of dicts with:
      direction, ema_N (bool: is trade direction aligned?), extra signals, r_result
    """
    records = []
    n_bars = len(bars)

    for i in range(2, n_bars - 1):
        c1_idx = i - 2
        c2_idx = i - 1
        c1, c2, c3 = bars[c1_idx], bars[c2_idx], bars[i + 1]

        c1_body  = abs(c1["close"] - c1["open"])
        c1_range = c1["high"] - c1["low"] or 1e-9
        c2_body  = abs(c2["close"] - c2["open"])
        c2_range = c2["high"] - c2["low"] or 1e-9

        a14   = atr14(bars, c2_idx)   or 1e-9
        a14_5 = atr14(bars, c2_idx-5) if c2_idx >= 5 else None
        av20  = avg_vol20(bars, c2_idx) or 1e-9

        for direction in ("bear", "bull"):
            bear = direction == "bear"

            # Base pattern check
            c1_green = c1["close"] > c1["open"]
            c1_red   = c1["close"] < c1["open"]
            c2_red   = c2["close"] < c2["open"]
            c2_green = c2["close"] > c2["open"]

            if bear:
                if not c1_green or c2["high"] < c1["high"] or not c2_red: continue
            else:
                if not c1_red  or c2["low"]  > c1["low"]  or not c2_green: continue

            r = sim_trade(c2, c3, direction)
            if r is None: continue

            # C3 opens at C2 close
            c3_open = c2["close"]

            # EMA alignment booleans (one per length)
            ema_align = {}
            for n in EMA_LENGTHS:
                e = emas_dict[n][c2_idx]
                if e is None:
                    ema_align[n] = None
                else:
                    ema_align[n] = (bear and c3_open < e) or (not bear and c3_open > e)

            # Extra signals
            c2_vol_spike = c2["vol"] / av20
            r_high = recent_high20(bars, c2_idx)
            r_low  = recent_low20(bars, c2_idx)

            trend_red   = sum(1 for j in range(max(0, c1_idx-3), c1_idx)
                              if bars[j]["close"] < bars[j]["open"])
            trend_green = sum(1 for j in range(max(0, c1_idx-3), c1_idx)
                              if bars[j]["close"] > bars[j]["open"])

            extra = {
                "vol_spike_1_5x": c2_vol_spike > 1.5,
                "near_extreme":   (bear and abs(c2["high"] - r_high) / r_high < 0.003) or
                                  (not bear and abs(c2["low"] - r_low) / r_low < 0.003),
                "c1_qual_hi":     c1_body / c1_range > 0.55,
                "atr_expanding":  (a14_5 is not None) and (a14 > a14_5),
                "c2_body_1atr":   (c2_body / a14) > 1.0,
                "trend_aligned":  (bear and trend_green >= 2) or
                                  (not bear and trend_red >= 2),
            }

            records.append({
                "direction": direction,
                "ema_align": ema_align,  # {20: bool/None, 50: bool/None, ...}
                "extra":     extra,
                "r":         r,
            })

    return records


# ── Analyse ────────────────────────────────────────────────────────────────────
def analyse(records, calendar_days):
    """
    For each EMA length: show baseline + each extra signal combo on top.
    """
    n_all = len(records)
    if n_all == 0:
        print("  No records."); return

    # Baseline (no EMA, no extra)
    b_wins  = sum(1 for rec in records if rec["r"] > 0)
    b_ev    = sum(rec["r"] for rec in records) / n_all
    b_wr    = b_wins / n_all * 100
    spd_all = n_all / calendar_days

    print(f"\n  Baseline (no filter): WR={b_wr:.1f}%  EV={b_ev:+.3f}R  "
          f"{n_all:,} setups  {spd_all:.1f}/day")
    print(f"\n  {'EMA':>5}  {'Aligned%':>9}  {'WR%':>7}  {'EV/trade':>9}  "
          f"{'Sig/day':>8}  {'Setups':>7}")
    print("  " + "-"*62)

    for n in EMA_LENGTHS:
        aligned = [rec for rec in records
                   if rec["ema_align"][n] is True]
        if len(aligned) < MIN_OCC:
            print(f"  EMA{n:>3}: not enough data")
            continue

        wins = sum(1 for rec in aligned if rec["r"] > 0)
        ev   = sum(rec["r"] for rec in aligned) / len(aligned)
        wr   = wins / len(aligned) * 100
        spd  = len(aligned) / calendar_days
        pct_kept = len(aligned) / n_all * 100

        print(f"  EMA{n:>3}  {pct_kept:>8.1f}%  {wr:>7.1f}  {ev:>+9.4f}  "
              f"{spd:>8.2f}  {len(aligned):>7,}")

    # Best EMA + extra signal combos
    print(f"\n  ── BEST COMBOS: EMA filter + extra signal ──")
    print(f"  {'EMA':>5}  {'Extra Signal':<22}  {'WR%':>7}  {'EV/trade':>9}  "
          f"{'Sig/day':>8}  {'Setups':>7}")
    print("  " + "-"*72)

    rows = []
    for n in EMA_LENGTHS:
        aligned = [rec for rec in records if rec["ema_align"][n] is True]
        if len(aligned) < MIN_OCC: continue

        for sig in EXTRA_SIGNALS:
            filtered = [rec for rec in aligned if rec["extra"][sig]]
            if len(filtered) < MIN_OCC: continue
            wins = sum(1 for rec in filtered if rec["r"] > 0)
            ev   = sum(rec["r"] for rec in filtered) / len(filtered)
            wr   = wins / len(filtered) * 100
            spd  = len(filtered) / calendar_days
            rows.append({"ema": n, "sig": sig, "wr": wr, "ev": ev,
                         "total": len(filtered), "spd": spd})

        # Pairs
        for s1, s2 in combinations(EXTRA_SIGNALS, 2):
            filtered = [rec for rec in aligned
                        if rec["extra"][s1] and rec["extra"][s2]]
            if len(filtered) < MIN_OCC: continue
            wins = sum(1 for rec in filtered if rec["r"] > 0)
            ev   = sum(rec["r"] for rec in filtered) / len(filtered)
            wr   = wins / len(filtered) * 100
            spd  = len(filtered) / calendar_days
            sig_label = f"{s1} + {s2}"
            rows.append({"ema": n, "sig": sig_label, "wr": wr, "ev": ev,
                         "total": len(filtered), "spd": spd})

    # Top 20 by EV
    rows.sort(key=lambda r: r["ev"], reverse=True)
    for row in rows[:20]:
        print(f"  EMA{row['ema']:>3}  {row['sig']:<22}  {row['wr']:>7.1f}  "
              f"{row['ev']:>+9.4f}  {row['spd']:>8.2f}  {row['total']:>7,}")

    # Sweet spot: 3-6 sig/day with EV > 0
    sweet = sorted([r for r in rows if 0.5 <= r["spd"] <= 6.0 and r["ev"] > 0],
                   key=lambda r: r["wr"], reverse=True)
    if sweet:
        print(f"\n  ── SWEET SPOT (0.5–6 sig/day, EV>0) ──")
        print(f"  {'EMA':>5}  {'Extra Signal':<22}  {'WR%':>7}  {'EV/trade':>9}  "
              f"{'Sig/day':>8}  {'Setups':>7}")
        print("  " + "-"*72)
        for row in sweet[:15]:
            print(f"  EMA{row['ema']:>3}  {row['sig']:<22}  {row['wr']:>7.1f}  "
                  f"{row['ev']:>+9.4f}  {row['spd']:>8.2f}  {row['total']:>7,}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    mode = "Real Binance" if USE_REAL else "Synthetic GBM"
    print(f"EMA-Filtered C1/C2/C3  |  BTC + ETH + SOL  |  {mode}")
    print(f"Bull: C3 above EMA only  |  Bear: C3 below EMA only")
    print(f"TP = {R_MULT}R  |  SL = C2 extreme\n")

    for tf, cfg in CONFIGS.items():
        calendar_days = cfg["sim_bars"] / cfg["bpd"]
        all_records   = []

        print(f"[{tf}]  {calendar_days:.0f} calendar days per asset")
        for asset, acfg in ASSETS.items():
            sigma = _sigma(cfg["minutes"], acfg["annual_vol"])
            bars = None
            if USE_REAL:
                try:
                    bars = fetch_binance(acfg["symbol"], tf, cfg["real_days"])
                except Exception as e:
                    print(f"    [{asset}] Binance error — synthetic.")

            if bars is None:
                bars = synthetic(cfg["sim_bars"], sigma, acfg["seed"], acfg["start_price"])

            emas_dict = compute_emas(bars, EMA_LENGTHS)
            recs = build_records(bars, emas_dict)
            all_records.extend(recs)
            print(f"    [{asset}] {len(recs):,} setups")

        print(f"  Combined: {len(all_records):,} setups across 3 assets")
        print(f"{'='*72}")

        analyse(all_records, calendar_days)
        print()


if __name__ == "__main__":
    main()
