"""
Mentos - Step 3: test real Finnhub price data access.

This settles a genuinely open question: does Finnhub's free tier
include historical daily candles, or are they gated to paid plans?
Search results disagreed on this earlier - this script gets the real
answer from your actual account instead of guessing from outdated
blog posts.

This does NOT place any trades. It only fetches price data and prints
it. Completely safe to run as many times as needed.
"""

from data.provider import FinnhubProvider, build_frame
import datetime as dt

if __name__ == "__main__":
    print("=" * 50)
    print("MENTOS - STEP 3: FINNHUB DATA ACCESS TEST")
    print("=" * 50)

    try:
        frame = build_frame(
            FinnhubProvider(), "finnhub", ["AAPL"],
            dt.date(2024, 1, 1), dt.date(2024, 3, 1),
        )
        symbols = frame.all_symbols()
        print(f"Symbols loaded: {symbols}")

        if not symbols:
            print()
            print("No symbols loaded - this usually means Finnhub returned")
            print("no data (check the FAILED message above, if any) or your")
            print("free tier doesn't include this endpoint.")
        else:
            date_range = frame.date_range("AAPL")
            print(f"Date range: {date_range}")
            bar = frame.latest_as_of("AAPL", dt.datetime(2024, 2, 1, 17, 0))
            print(f"Sample bar: {bar}")
            print()
            print("SUCCESS - real Finnhub price data confirmed working.")
    except PermissionError as e:
        print()
        print("CONFIRMED: 403 Permission Error")
        print(str(e))
        print()
        print("This means historical candles are gated on your current")
        print("Finnhub plan. Next step: switch to Stooq or a manual CSV")
        print("for price history instead.")
    except Exception as e:
        print()
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
