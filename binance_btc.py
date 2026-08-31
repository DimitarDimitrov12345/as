"""
Binance public API client for BTC market data.
No API key required for public endpoints.
"""

import urllib.request
import urllib.parse
import json
from datetime import datetime


BASE_URL = "https://api.binance.com/api/v3"


def _get(endpoint: str, params: dict = None) -> dict | list:
    url = f"{BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode())


def get_btc_price() -> dict:
    """Current BTC/USDT ticker price."""
    return _get("ticker/price", {"symbol": "BTCUSDT"})


def get_btc_24h_stats() -> dict:
    """24-hour rolling window statistics for BTC/USDT."""
    return _get("ticker/24hr", {"symbol": "BTCUSDT"})


def get_btc_order_book(limit: int = 10) -> dict:
    """BTC/USDT order book (top bids and asks)."""
    return _get("depth", {"symbol": "BTCUSDT", "limit": limit})


def get_btc_klines(interval: str = "1h", limit: int = 24) -> list:
    """
    BTC/USDT candlestick data.
    interval options: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h,
                      1d, 3d, 1w, 1M
    """
    data = _get("klines", {"symbol": "BTCUSDT", "interval": interval, "limit": limit})
    candles = []
    for row in data:
        candles.append({
            "open_time": datetime.fromtimestamp(row[0] / 1000).isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": datetime.fromtimestamp(row[6] / 1000).isoformat(),
        })
    return candles


def get_btc_recent_trades(limit: int = 10) -> list:
    """Most recent BTC/USDT trades."""
    trades = _get("trades", {"symbol": "BTCUSDT", "limit": limit})
    for t in trades:
        t["time"] = datetime.fromtimestamp(t["time"] / 1000).isoformat()
        t["price"] = float(t["price"])
        t["qty"] = float(t["qty"])
    return trades


def main():
    print("=" * 55)
    print("  Binance BTC/USDT Market Data")
    print("=" * 55)

    # Current price
    price_data = get_btc_price()
    print(f"\nCurrent price : ${float(price_data['price']):,.2f} USDT")

    # 24h stats
    stats = get_btc_24h_stats()
    print(f"\n--- 24-Hour Stats ---")
    print(f"  Open         : ${float(stats['openPrice']):,.2f}")
    print(f"  High         : ${float(stats['highPrice']):,.2f}")
    print(f"  Low          : ${float(stats['lowPrice']):,.2f}")
    print(f"  Last price   : ${float(stats['lastPrice']):,.2f}")
    print(f"  Price change : ${float(stats['priceChange']):+,.2f}  ({float(stats['priceChangePercent']):+.2f}%)")
    print(f"  Volume (BTC) : {float(stats['volume']):,.4f}")
    print(f"  Volume (USDT): ${float(stats['quoteVolume']):,.2f}")

    # Order book
    book = get_btc_order_book(limit=5)
    print(f"\n--- Order Book (top 5) ---")
    print(f"  {'Ask Price':>14}  {'Ask Qty':>12}    {'Bid Price':>14}  {'Bid Qty':>12}")
    for ask, bid in zip(book["asks"], book["bids"]):
        print(f"  {float(ask[0]):>14,.2f}  {float(ask[1]):>12,.6f}    "
              f"{float(bid[0]):>14,.2f}  {float(bid[1]):>12,.6f}")

    # Last 5 klines (1-hour)
    klines = get_btc_klines(interval="1h", limit=5)
    print(f"\n--- Last 5 Hourly Candles ---")
    print(f"  {'Open Time':<22}  {'Open':>12}  {'High':>12}  {'Low':>12}  {'Close':>12}  {'Volume':>12}")
    for c in klines:
        print(f"  {c['open_time']:<22}  {c['open']:>12,.2f}  {c['high']:>12,.2f}  "
              f"  {c['low']:>12,.2f}  {c['close']:>12,.2f}  {c['volume']:>12,.4f}")

    # Recent trades
    trades = get_btc_recent_trades(limit=5)
    print(f"\n--- Last 5 Trades ---")
    print(f"  {'Time':<22}  {'Price':>12}  {'Qty':>10}  Side")
    for t in trades:
        side = "SELL" if t["isBuyerMaker"] else "BUY"
        print(f"  {t['time']:<22}  {t['price']:>12,.2f}  {t['qty']:>10,.6f}  {side}")

    print()


if __name__ == "__main__":
    main()
