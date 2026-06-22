"""
Mentos - Step 4: test real Stooq price data access.

Stooq requires no API key and no account - it's a free public CSV
endpoint. This is the fallback since Finnhub's free tier doesn't
include historical candles on this account (confirmed via Step 3).

This does NOT place any trades. Completely safe to run repeatedly.
"""

from data.provider import StooqProvider, build_frame
import datetime as dt

if __name__ == "__main__":
    print("=" * 50)
    print("MENTOS - STEP 4: STOOQ DATA ACCESS TEST")
    print("=" * 50)

    try:
        frame = build_frame(
            StooqProvider(), "stooq_eod", ["AAPL", "MSFT"],
            dt.date(2024, 1, 1), dt.date(2024, 3, 1),
        )
        symbols = frame.all_symbols()
        print(f"Symbols loaded: {symbols}")

        if not symbols:
            print()
            print("No symbols loaded. Stooq may have changed its response")
            print("format, or this network may be blocking the request.")
        else:
            for sym in symbols:
                date_range = frame.date_range(sym)
                print(f"{sym} date range: {date_range}")
            bar = frame.latest_as_of("AAPL", dt.datetime(2024, 2, 1, 17, 0))
            print(f"Sample AAPL bar: {bar}")
            print()
            print("SUCCESS - real Stooq price data confirmed working.")
            print("This is genuinely real, free, no-signup market data.")
    except Exception as e:
        print()
        print(f"ERROR: {type(e).__name__}: {e}")
