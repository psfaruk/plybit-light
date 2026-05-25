"""Quick candle fetch test — run: python test_candles.py"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# Load .env
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import config
import quotex

async def main():
    print(f"Token : {config.QUOTEX_SESSION_TOKEN[:8]}…")
    print()

    pair = "EURUSD"
    tf   = 60
    print(f"Fetching last 5000 candles for {pair} @ {tf}s …")

    candles = await quotex.fetch_history(pair, tf, count=5000)

    if not candles:
        print("ERROR: 0 candles returned — check session token / connection")
        return

    print(f"OK: got {len(candles)} candles")
    first = candles[0]
    last  = candles[-1]
    from datetime import datetime, timezone
    fmt = lambda ep: datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"  oldest : {fmt(first['epoch'])}  close={first['close']}")
    print(f"  newest : {fmt(last['epoch'])}   close={last['close']}")

asyncio.run(main())
