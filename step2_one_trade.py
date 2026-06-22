"""
Mentos - Step 2: place ONE real (paper) trade.

This script does exactly one thing: buys 1 share of AAPL in your PAPER
account, prints the result, and stops. No strategy, no automation, no
loop. The goal is to prove order placement works and let you SEE a
trade happen before anything runs automatically or repeatedly.

Safety: AlpacaPaperClient defaults to the paper endpoint and there is
no code path here that touches live trading.

Run this ONCE. Check the output. Then check your Alpaca dashboard
(Positions or Orders tab) to see the trade show up there too - seeing
it in two places (this script's output AND Alpaca's own dashboard)
confirms it's real, not just a print statement.
"""

from execution.alpaca_client import AlpacaPaperClient

SYMBOL = "AAPL"
QTY = 1  # exactly one share - deliberately tiny for a first test

if __name__ == "__main__":
    print("=" * 50)
    print(f"MENTOS - STEP 2: PLACING ONE TEST TRADE ({QTY} share of {SYMBOL})")
    print("=" * 50)

    client = AlpacaPaperClient(allow_live=False)
    print(f"Confirmed paper trading: {client.is_paper}")
    print(f"Endpoint: {client.base_url}")
    print()

    account_before = client.get_account()
    print(f"Cash before trade: ${account_before.get('cash')}")
    print()

    print(f"Placing market BUY order: {QTY} share(s) of {SYMBOL}...")
    result = client.place_market_order(symbol=SYMBOL, qty=QTY, side="buy")

    print()
    if result.success:
        print("ORDER SUBMITTED SUCCESSFULLY")
        print(f"  Order ID: {result.order_id}")
        print(f"  Symbol:   {result.symbol}")
        print(f"  Side:     {result.side}")
        print(f"  Qty:      {result.qty}")
        print(f"  Status:   {result.status}")
        print()
        print("Check your Alpaca dashboard's Orders/Positions tab to see")
        print("this same trade reflected there.")
    else:
        print("ORDER FAILED")
        print(f"  Error: {result.error}")
