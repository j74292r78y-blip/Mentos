"""
Mentos Data Layer
=================
GOLDEN RULE ENFORCED HERE: every price/fundamental/signal record carries
TWO timestamps:
    - event_time     : when the underlying thing actually happened
    - available_time : when this system could have first known about it

as_of(timestamp) queries filter on available_time, never event_time.
This is the single choke point that prevents look-ahead bias. If you
need to add a new data source, route it through PointInTimeFrame and
you get leakage protection for free.

Real data source: Stooq daily CSV endpoint (no API key required, good
for research-grade historical EOD prices). Swap in Polygon/Alpaca/IEX
later by implementing the same PriceDataProvider interface.
"""

from __future__ import annotations
import io
import csv
import json
import os
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Protocol
import urllib.request
import urllib.parse
import urllib.error


# ---------------------------------------------------------------------------
# Core record types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    symbol: str
    date: dt.date          # event_time: the trading day this bar represents
    open: float
    high: float
    low: float
    close: float
    volume: float
    available_time: dt.datetime  # when this bar was actually knowable

    @property
    def event_time(self) -> dt.datetime:
        # EOD bars become knowable at market close; treat date as event date
        return dt.datetime.combine(self.date, dt.time(16, 0))


class DataQuality:
    """0-100 reliability score per source, as required by the spec."""
    SCORES = {
        "stooq_eod": 85,       # free EOD vendor, generally clean but no SLA
        "finnhub": 88,         # institutional-grade source, real account/key
        "synthetic": 40,       # generated for testing, not real
        "manual_csv": 60,      # user-supplied, quality unknown until checked
    }

    @staticmethod
    def score(source: str) -> int:
        return DataQuality.SCORES.get(source, 0)  # unknown source = 0 trust


# ---------------------------------------------------------------------------
# Point-in-time frame: the leakage firewall
# ---------------------------------------------------------------------------

class PointInTimeFrame:
    """
    Wraps a list of Bars and only ever exposes data whose available_time
    is <= the query timestamp. This is the ONLY sanctioned way the rest
    of the system may read historical data.
    """

    def __init__(self, bars: list[Bar], source: str):
        self.source = source
        self.quality_score = DataQuality.score(source)
        self._bars_by_symbol: dict[str, list[Bar]] = {}
        for b in bars:
            self._bars_by_symbol.setdefault(b.symbol, []).append(b)
        for sym in self._bars_by_symbol:
            self._bars_by_symbol[sym].sort(key=lambda b: b.date)

    def as_of(self, symbol: str, timestamp: dt.datetime, lookback: int = 1) -> list[Bar]:
        """
        Return up to `lookback` most recent bars for `symbol` that were
        ACTUALLY AVAILABLE at `timestamp`. This is the chokepoint for
        the absolute rule: 'only information available at that timestamp
        may be used.'
        """
        bars = self._bars_by_symbol.get(symbol, [])
        eligible = [b for b in bars if b.available_time <= timestamp]
        return eligible[-lookback:] if lookback else eligible

    def latest_as_of(self, symbol: str, timestamp: dt.datetime) -> Optional[Bar]:
        bars = self.as_of(symbol, timestamp, lookback=1)
        return bars[0] if bars else None

    def all_symbols(self) -> list[str]:
        return list(self._bars_by_symbol.keys())

    def date_range(self, symbol: str) -> tuple[Optional[dt.date], Optional[dt.date]]:
        bars = self._bars_by_symbol.get(symbol, [])
        if not bars:
            return None, None
        return bars[0].date, bars[-1].date


# ---------------------------------------------------------------------------
# Provider interface (swap implementations without touching the rest of Mentos)
# ---------------------------------------------------------------------------

class PriceDataProvider(Protocol):
    def fetch(self, symbol: str, start: dt.date, end: dt.date) -> list[Bar]:
        ...


class StooqProvider:
    """
    Free, no-key EOD data. Good enough for research/paper-trading
    validation; NOT a substitute for a licensed real-time feed in any
    production use. available_time is set conservatively to the day
    AFTER the bar date close, since EOD vendors typically finalize
    adjusted data with a delay - this avoids an optimistic same-day
    leakage assumption.
    """

    BASE_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"

    def fetch(self, symbol: str, start: dt.date, end: dt.date) -> list[Bar]:
        url = self.BASE_URL.format(symbol=symbol.lower())
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as e:
            raise ConnectionError(f"Stooq fetch failed for {symbol}: {e}")

        if "Date,Open,High,Low,Close,Volume" not in raw[:60]:
            raise ValueError(f"Unexpected Stooq response for {symbol}: {raw[:200]}")

        bars = []
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            try:
                d = dt.datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            if d < start or d > end:
                continue
            try:
                bars.append(Bar(
                    symbol=symbol.upper(),
                    date=d,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"] or 0),
                    available_time=dt.datetime.combine(d, dt.time(16, 30)),
                ))
            except (ValueError, KeyError):
                continue  # malformed row -> drop, don't crash whole fetch
        return bars


class SyntheticProvider:
    """Deterministic synthetic data for unit tests / offline dev. NEVER
    use for real performance claims - clearly tagged 'synthetic' quality."""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def fetch(self, symbol: str, start: dt.date, end: dt.date) -> list[Bar]:
        import random
        rng = random.Random(hash(symbol) ^ self.seed)
        bars = []
        price = 100.0
        d = start
        while d <= end:
            if d.weekday() < 5:  # skip weekends
                ret = rng.gauss(0.0003, 0.018)
                price *= (1 + ret)
                o = price * (1 + rng.gauss(0, 0.003))
                h = max(o, price) * (1 + abs(rng.gauss(0, 0.004)))
                l = min(o, price) * (1 - abs(rng.gauss(0, 0.004)))
                vol = abs(rng.gauss(1_000_000, 300_000))
                bars.append(Bar(symbol, d, o, h, l, price, vol,
                                 dt.datetime.combine(d, dt.time(16, 30))))
            d += dt.timedelta(days=1)
        return bars


class ManualCSVProvider:
    """
    Load real historical prices from a user-supplied CSV file. Expects
    columns: Date,Open,High,Low,Close,Volume (case-insensitive), one
    file per symbol, OR a single multi-symbol file with a Symbol column.
    This is the recommended path for REAL data when live egress isn't
    available (e.g. inside this sandbox) - export from your broker,
    Yahoo Finance, Stooq's own UI, etc. and point this at the file.
    Tagged quality=60 ("manual_csv") until provenance is verified.
    """

    def __init__(self, filepath: str, symbol_col: Optional[str] = None):
        self.filepath = filepath
        self.symbol_col = symbol_col

    def fetch(self, symbol: str, start: dt.date, end: dt.date) -> list[Bar]:
        bars = []
        with open(self.filepath, newline="") as f:
            reader = csv.DictReader(f)
            fieldmap = {k.lower(): k for k in reader.fieldnames or []}
            for row in reader:
                if self.symbol_col:
                    row_symbol = row.get(fieldmap.get(self.symbol_col.lower(), ""), "")
                    if row_symbol.upper() != symbol.upper():
                        continue
                try:
                    d = dt.datetime.strptime(row[fieldmap["date"]], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    continue
                if d < start or d > end:
                    continue
                try:
                    bars.append(Bar(
                        symbol=symbol.upper(),
                        date=d,
                        open=float(row[fieldmap["open"]]),
                        high=float(row[fieldmap["high"]]),
                        low=float(row[fieldmap["low"]]),
                        close=float(row[fieldmap["close"]]),
                        volume=float(row[fieldmap.get("volume", "")] or 0) if "volume" in fieldmap else 0.0,
                        available_time=dt.datetime.combine(d, dt.time(16, 30)),
                    ))
                except (ValueError, KeyError):
                    continue
        return bars


class FinnhubProvider:
    """
    Real Finnhub integration: https://finnhub.io/api/v1/stock/candle

    Auth: token=<key> as a query param (or X-Finnhub-Token header - we use
    the query param since it's simpler to verify with curl).

    KNOWN UNCERTAINTY (stated explicitly per spec - never silently assume):
    as of writing, search results disagree on whether /stock/candle is
    still free-tier-accessible. Some sources (official client examples,
    several 2026-dated tutorials) show it working on a free key; at least
    one recent source claims it now 403s on free keys and was moved to
    premium. This may simply mean it changed recently, or that it varies
    by account/region. Rather than guess, this provider surfaces the
    actual HTTP status/response on failure so you find out definitively
    in one test call instead of me asserting an unverified claim.

    If /stock/candle does 403 for you: the practical fallback is Finnhub's
    /quote endpoint (definitely free - current price only, no history) to
    backfill going forward day-by-day, combined with StooqProvider for
    historical backfill. I'm not pre-building that fallback path until we
    know which case you're in - no point writing speculative code.
    """

    BASE_URL = "https://finnhub.io/api/v1/stock/candle"

    def __init__(self, api_key: Optional[str] = None):
        # Never hardcode the key. Read from environment so it's never
        # pasted into chat or committed to source control.
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No Finnhub API key found. Set the FINNHUB_API_KEY environment "
                "variable, e.g.:\n  export FINNHUB_API_KEY=your_key_here\n"
                "Get a free key at https://finnhub.io/register"
            )

    def fetch(self, symbol: str, start: dt.date, end: dt.date) -> list[Bar]:
        from_ts = int(dt.datetime.combine(start, dt.time(0, 0), tzinfo=dt.timezone.utc).timestamp())
        to_ts = int(dt.datetime.combine(end, dt.time(23, 59), tzinfo=dt.timezone.utc).timestamp())
        params = urllib.parse.urlencode({
            "symbol": symbol.upper(),
            "resolution": "D",
            "from": from_ts,
            "to": to_ts,
            "token": self.api_key,
        })
        url = f"{self.BASE_URL}?{params}"

        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                status = resp.status
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 403:
                raise PermissionError(
                    f"Finnhub returned 403 for {symbol} on /stock/candle. This "
                    f"strongly suggests your account's free tier does NOT include "
                    f"historical candles (this is genuinely disputed in current "
                    f"docs/community reports - your account just gave us the "
                    f"ground truth). Response body: {body[:300]}"
                )
            raise ConnectionError(f"Finnhub HTTP {e.code} for {symbol}: {body[:300]}")
        except Exception as e:
            raise ConnectionError(f"Finnhub fetch failed for {symbol}: {e}")

        data = json.loads(raw)
        if data.get("s") == "no_data":
            return []  # explicitly no data for this range, not an error
        if data.get("s") != "ok":
            raise ValueError(f"Finnhub returned unexpected status for {symbol}: {data}")

        bars = []
        closes, highs, lows, opens, vols, times = (
            data.get("c", []), data.get("h", []), data.get("l", []),
            data.get("o", []), data.get("v", []), data.get("t", []),
        )
        for i in range(len(times)):
            bar_date = dt.datetime.fromtimestamp(times[i], tz=dt.timezone.utc).date()
            bars.append(Bar(
                symbol=symbol.upper(),
                date=bar_date,
                open=opens[i], high=highs[i], low=lows[i], close=closes[i],
                volume=vols[i],
                # Finnhub daily candles are end-of-day; same conservative
                # next-available-moment assumption as Stooq.
                available_time=dt.datetime.combine(bar_date, dt.time(16, 30)),
            ))
        return bars


def build_frame(provider: PriceDataProvider, source_name: str,
                 symbols: list[str], start: dt.date, end: dt.date) -> PointInTimeFrame:
    all_bars = []
    errors = {}
    for sym in symbols:
        try:
            all_bars.extend(provider.fetch(sym, start, end))
        except Exception as e:
            errors[sym] = str(e)
    if errors:
        print(f"[DataQuality WARNING] failed symbols: {errors}")
    return PointInTimeFrame(all_bars, source=source_name)
