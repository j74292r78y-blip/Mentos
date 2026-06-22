"""
Mentos Alpaca Connector
========================
Real paper-trading execution via Alpaca's API. This is the ONLY file in
Mentos that actually places trades - everything else (signals, risk,
backtest) is research/simulation. Trades placed here are PAPER trades
(fake money) by construction, because we point at Alpaca's paper base
URL, never the live one.

SAFETY: this client refuses to run against the live trading URL unless
you explicitly pass allow_live=True AND set a separate environment
variable confirming it. There is no path to accidentally going live.

Auth: Alpaca uses two headers, not a single token:
    APCA-API-KEY-ID: <your key id>
    APCA-API-SECRET-KEY: <your secret>
Never hardcode these - always read from environment variables.
"""

from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional


PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
LIVE_BASE_URL = "https://api.alpaca.markets/v2"  # never used unless explicitly unlocked


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    symbol: str
    side: str
    qty: float
    status: Optional[str]
    raw_response: dict
    error: Optional[str] = None


class AlpacaPaperClient:
    """
    Minimal, deliberately narrow client: only what Mentos needs (account
    info, place a market order, check positions). Not a general-purpose
    Alpaca SDK - smaller surface area is easier to audit for safety.
    """

    def __init__(self, allow_live: bool = False):
        self.key_id = os.environ.get("ALPACA_API_KEY_ID")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY")
        if not self.key_id or not self.secret_key:
            raise ValueError(
                "Missing Alpaca credentials. Set environment variables:\n"
                "  ALPACA_API_KEY_ID=your_key_id\n"
                "  ALPACA_SECRET_KEY=your_secret\n"
                "Get these from your Alpaca dashboard (Paper Trading account)."
            )

        # Hard safety gate: live trading requires BOTH the explicit flag
        # AND a separate confirmation env var. One alone is not enough.
        # This is intentionally annoying - going live should never be
        # the accidental default path.
        if allow_live:
            confirm = os.environ.get("MENTOS_CONFIRM_LIVE_TRADING")
            if confirm != "I_UNDERSTAND_THIS_IS_REAL_MONEY":
                raise PermissionError(
                    "allow_live=True was set but MENTOS_CONFIRM_LIVE_TRADING "
                    "env var does not match the required confirmation string. "
                    "Refusing to connect to the live trading endpoint."
                )
            self.base_url = LIVE_BASE_URL
            self.is_paper = False
        else:
            self.base_url = PAPER_BASE_URL
            self.is_paper = True

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise ConnectionError(f"Alpaca {method} {path} failed ({e.code}): {error_body[:300]}")

    def get_account(self) -> dict:
        """Returns account info: cash, buying_power, portfolio_value, etc."""
        return self._request("GET", "/account")

    def get_positions(self) -> list[dict]:
        return self._request("GET", "/positions")

    def place_market_order(self, symbol: str, qty: float, side: str) -> OrderResult:
        """
        side must be 'buy' or 'sell'. This is a MARKET order (fills at
        current price) for simplicity - no limit price logic yet.
        Every call here hits whichever base_url was set at __init__,
        which is paper unless explicitly unlocked above.
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        body = {
            "symbol": symbol.upper(),
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            response = self._request("POST", "/orders", body)
            return OrderResult(
                success=True,
                order_id=response.get("id"),
                symbol=symbol.upper(),
                side=side,
                qty=qty,
                status=response.get("status"),
                raw_response=response,
            )
        except ConnectionError as e:
            return OrderResult(
                success=False, order_id=None, symbol=symbol.upper(), side=side,
                qty=qty, status=None, raw_response={}, error=str(e),
            )


def test_connection() -> str:
    """
    Run this FIRST, before placing any order. Confirms credentials work
    and - critically - confirms you are pointed at the PAPER endpoint,
    not live. Prints account status so you can visually verify.
    """
    client = AlpacaPaperClient(allow_live=False)
    account = client.get_account()
    lines = [
        f"Connected to: {client.base_url}",
        f"Paper trading confirmed: {client.is_paper}",
        f"Account status: {account.get('status')}",
        f"Cash: ${account.get('cash')}",
        f"Buying power: ${account.get('buying_power')}",
        f"Portfolio value: ${account.get('portfolio_value')}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(test_connection())
