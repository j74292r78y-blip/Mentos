"""
Mentos - Step 1 connection test.

This is the FIRST thing to run once this code is on Render. It does NOT
place any trades. It only confirms:
  1. Your Alpaca paper trading credentials work
  2. You are definitely connected to the PAPER endpoint, not live
  3. Your fake starting balance shows up correctly

Run this before anything else. If this doesn't print a paper account
with cash in it, stop and fix that first - nothing past this point will
work safely otherwise.
"""

from execution.alpaca_client import test_connection

if __name__ == "__main__":
    print("=" * 50)
    print("MENTOS - ALPACA PAPER TRADING CONNECTION TEST")
    print("=" * 50)
    try:
        result = test_connection()
        print(result)
        print()
        print("SUCCESS - paper trading connection confirmed.")
    except Exception as e:
        print(f"FAILED: {e}")
        print()
        print("Check that ALPACA_API_KEY_ID and ALPACA_SECRET_KEY are set")
        print("correctly in Render's environment variables.")
