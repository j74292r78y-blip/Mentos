"""
Mentos Signal Engine
====================
Per spec: "No signal is permanent. Signal strength is not assumed - it
is learned." Every signal is registered as a Hypothesis with explicit
fields (inputs, logic, expected outcome, failure conditions, metrics).
Signals emit a value in [-1, +1] (bearish -> bullish) plus a raw
confidence, but RAW CONFIDENCE IS NOT TRUSTED until CalibrationTracker
proves it behaves like a real probability historically.
"""

from __future__ import annotations
import datetime as dt
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from data.provider import PointInTimeFrame


class MarketRegime(str, Enum):
    BULL = "bull_trend"
    BEAR = "bear_trend"
    SIDEWAYS = "sideways_mean_reverting"
    HIGH_VOL = "high_volatility"
    LOW_VOL = "low_volatility"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    UNKNOWN = "unknown"


@dataclass
class SignalHypothesis:
    """Mandatory documentation block - the spec requires this to exist
    for EVERY signal before it can be tested, let alone deployed."""
    name: str
    hypothesis: str                 # what is being tested
    inputs: list[str]                # data sources used
    signal_logic: str                # human-readable description of the math
    expected_outcome: str
    failure_conditions: str          # what observation would falsify this
    measurement_metrics: list[str]   # e.g. ["IC", "win_rate", "sharpe_contrib"]
    status: str = "untested"         # untested | active | deprecated | rejected

    def validate_testable(self) -> bool:
        """A hypothesis with no measurement metric is, by spec, invalid."""
        return bool(self.measurement_metrics) and bool(self.failure_conditions)


@dataclass
class SignalReading:
    signal_name: str
    symbol: str
    timestamp: dt.datetime
    value: float          # -1 (max bearish) to +1 (max bullish)
    raw_confidence: float  # 0-1, UNCALIBRATED until proven otherwise
    evidence: dict = field(default_factory=dict)  # explainability payload


class Signal:
    """Base class. Subclass and implement `compute`. `compute` MUST only
    use frame.as_of()/latest_as_of() - never raw frame internals - or
    you reintroduce look-ahead bias."""

    hypothesis: SignalHypothesis

    def compute(self, frame: PointInTimeFrame, symbol: str,
                timestamp: dt.datetime) -> Optional[SignalReading]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Example concrete signal: simple momentum (illustrative, NOT assumed good)
# ---------------------------------------------------------------------------

class MomentumSignal(Signal):
    """
    HYPOTHESIS: stocks with positive N-day price momentum continue to
    outperform over the subsequent holding period (classic momentum
    factor). This is explicitly marked untested until SignalValidator
    runs IC/decay analysis - the spec forbids assuming it works.
    """

    def __init__(self, lookback_days: int = 20):
        self.lookback_days = lookback_days
        self.hypothesis = SignalHypothesis(
            name=f"momentum_{lookback_days}d",
            hypothesis=f"{lookback_days}-day trailing return predicts forward return direction",
            inputs=["price_close"],
            signal_logic=f"value = clip(return over trailing {lookback_days} trading days / 0.20, -1, 1)",
            expected_outcome="positive information coefficient (IC) with forward returns, decaying over time",
            failure_conditions="IC indistinguishable from zero (|IC| < 0.02) across walk-forward folds, or sign-unstable across regimes",
            measurement_metrics=["information_coefficient", "decay_half_life", "regime_conditional_sharpe"],
        )

    def compute(self, frame: PointInTimeFrame, symbol: str,
                timestamp: dt.datetime) -> Optional[SignalReading]:
        bars = frame.as_of(symbol, timestamp, lookback=self.lookback_days + 1)
        if len(bars) < self.lookback_days + 1:
            return None  # insufficient history - explicitly state missing data, never fabricate
        start_price = bars[0].close
        end_price = bars[-1].close
        if start_price <= 0:
            return None
        ret = (end_price - start_price) / start_price
        value = max(-1.0, min(1.0, ret / 0.20))
        # raw_confidence here is a placeholder strength proxy, NOT a
        # probability - it must pass through CalibrationTracker before
        # being treated as one.
        raw_confidence = min(1.0, abs(ret) / 0.10)
        return SignalReading(
            signal_name=self.hypothesis.name,
            symbol=symbol,
            timestamp=timestamp,
            value=value,
            raw_confidence=raw_confidence,
            evidence={
                "lookback_days": self.lookback_days,
                "start_price": start_price,
                "end_price": end_price,
                "raw_return": ret,
            },
        )


class MeanReversionSignal(Signal):
    """
    HYPOTHESIS: short-term deviation from a moving average mean-reverts.
    Directionally OPPOSITE of momentum on purpose - used later to show
    how regime conditioning should determine which one gets weight.
    """

    def __init__(self, window: int = 10):
        self.window = window
        self.hypothesis = SignalHypothesis(
            name=f"mean_reversion_{window}d",
            hypothesis=f"price deviations from {window}-day SMA revert toward the mean",
            inputs=["price_close"],
            signal_logic="value = -clip((price - SMA)/SMA / 0.05, -1, 1)",
            expected_outcome="positive IC in sideways/low-vol regimes, negative or zero IC in trending regimes",
            failure_conditions="IC near zero or sign-unstable across walk-forward folds in the SAME regime",
            measurement_metrics=["information_coefficient", "decay_half_life", "regime_conditional_sharpe"],
        )

    def compute(self, frame: PointInTimeFrame, symbol: str,
                timestamp: dt.datetime) -> Optional[SignalReading]:
        bars = frame.as_of(symbol, timestamp, lookback=self.window)
        if len(bars) < self.window:
            return None
        closes = [b.close for b in bars]
        sma = sum(closes) / len(closes)
        if sma <= 0:
            return None
        price = closes[-1]
        deviation = (price - sma) / sma
        value = max(-1.0, min(1.0, -deviation / 0.05))
        raw_confidence = min(1.0, abs(deviation) / 0.03)
        return SignalReading(
            signal_name=self.hypothesis.name,
            symbol=symbol,
            timestamp=timestamp,
            value=value,
            raw_confidence=raw_confidence,
            evidence={"window": self.window, "sma": sma, "price": price, "deviation": deviation},
        )


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------

class RegimeDetector:
    """
    Classifies market regime from a benchmark series (e.g. SPY) as-of a
    timestamp. Deliberately simple/transparent rules (spec demands
    explainability over sophistication where they trade off).
    """

    def __init__(self, trend_window: int = 50, vol_window: int = 20):
        self.trend_window = trend_window
        self.vol_window = vol_window

    def classify(self, frame: PointInTimeFrame, benchmark_symbol: str,
                 timestamp: dt.datetime) -> tuple[MarketRegime, dict]:
        bars = frame.as_of(benchmark_symbol, timestamp, lookback=self.trend_window)
        if len(bars) < self.trend_window:
            return MarketRegime.UNKNOWN, {"reason": "insufficient_history", "bars_available": len(bars)}

        closes = [b.close for b in bars]
        sma_long = sum(closes) / len(closes)
        price = closes[-1]
        trend_pct = (price - sma_long) / sma_long

        recent = closes[-self.vol_window:]
        rets = [(recent[i] - recent[i-1]) / recent[i-1] for i in range(1, len(recent))]
        vol = (sum((r - sum(rets)/len(rets))**2 for r in rets) / len(rets)) ** 0.5 if len(rets) > 1 else 0.0
        annualized_vol = vol * math.sqrt(252)

        evidence = {
            "trend_pct_vs_sma": trend_pct,
            "annualized_vol": annualized_vol,
            "sma_window": self.trend_window,
            "vol_window": self.vol_window,
        }

        # Vol regime takes priority signal-wise since it gates risk sizing
        if annualized_vol > 0.30:
            regime = MarketRegime.HIGH_VOL
        elif annualized_vol < 0.12:
            regime = MarketRegime.LOW_VOL
        elif trend_pct > 0.03:
            regime = MarketRegime.BULL
        elif trend_pct < -0.03:
            regime = MarketRegime.BEAR
        else:
            regime = MarketRegime.SIDEWAYS

        return regime, evidence


# ---------------------------------------------------------------------------
# Calibration tracking - "confidence must earn trust through evidence"
# ---------------------------------------------------------------------------

@dataclass
class CalibrationBucket:
    confidence_low: float
    confidence_high: float
    predictions: int = 0
    correct: int = 0

    @property
    def empirical_accuracy(self) -> Optional[float]:
        return self.correct / self.predictions if self.predictions > 0 else None


class CalibrationTracker:
    """
    Buckets predictions by stated confidence and checks whether stated
    confidence matches empirical hit rate. Per spec: '70% confidence
    must behave like 70% correctness over time.' Until enough samples
    exist, calibration is explicitly UNKNOWN - never silently assumed.
    """

    def __init__(self, n_buckets: int = 5, min_samples_for_calibration: int = 30):
        edges = [i / n_buckets for i in range(n_buckets + 1)]
        self.buckets = [CalibrationBucket(edges[i], edges[i+1]) for i in range(n_buckets)]
        self.min_samples = min_samples_for_calibration

    def record(self, stated_confidence: float, was_correct: bool):
        for b in self.buckets:
            if b.confidence_low <= stated_confidence <= b.confidence_high:
                b.predictions += 1
                if was_correct:
                    b.correct += 1
                return

    def report(self) -> list[dict]:
        out = []
        for b in self.buckets:
            status = "unknown_insufficient_data" if b.predictions < self.min_samples else "calibrated_estimate"
            out.append({
                "confidence_range": f"{b.confidence_low:.0%}-{b.confidence_high:.0%}",
                "n_predictions": b.predictions,
                "empirical_accuracy": b.empirical_accuracy,
                "status": status,
            })
        return out

    def calibration_error(self) -> Optional[float]:
        """Mean absolute error between stated and empirical, weighted
        by sample count. Returns None if insufficient data anywhere."""
        total_n = sum(b.predictions for b in self.buckets)
        if total_n < self.min_samples:
            return None
        midpoints_err = 0.0
        for b in self.buckets:
            if b.predictions == 0:
                continue
            midpoint = (b.confidence_low + b.confidence_high) / 2
            midpoints_err += b.predictions * abs(midpoint - (b.empirical_accuracy or 0))
        return midpoints_err / total_n


# ---------------------------------------------------------------------------
# Signal decay analysis - required windows: 1d,3d,1w,1m,3m,6m
# ---------------------------------------------------------------------------

DECAY_WINDOWS_DAYS = {"1d": 1, "3d": 3, "1w": 5, "1m": 21, "3m": 63, "6m": 126}


def information_coefficient(readings: list[SignalReading], forward_returns: dict[tuple[str, dt.datetime], float]) -> Optional[float]:
    """
    Spearman-style rank IC between signal value and forward return.
    Returns None (explicitly) if insufficient paired data exists -
    spec requires stating missing data rather than fabricating a number.
    """
    pairs = []
    for r in readings:
        key = (r.symbol, r.timestamp)
        if key in forward_returns:
            pairs.append((r.value, forward_returns[key]))
    if len(pairs) < 10:
        return None

    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0] * len(vals)
        for rank_pos, idx in enumerate(order):
            ranks[idx] = rank_pos
        return ranks

    sig_vals = [p[0] for p in pairs]
    ret_vals = [p[1] for p in pairs]
    sig_ranks = rank(sig_vals)
    ret_ranks = rank(ret_vals)
    n = len(pairs)
    mean_sr = sum(sig_ranks) / n
    mean_rr = sum(ret_ranks) / n
    cov = sum((sig_ranks[i] - mean_sr) * (ret_ranks[i] - mean_rr) for i in range(n))
    var_s = sum((sig_ranks[i] - mean_sr) ** 2 for i in range(n))
    var_r = sum((ret_ranks[i] - mean_rr) ** 2 for i in range(n))
    if var_s == 0 or var_r == 0:
        return 0.0
    return cov / math.sqrt(var_s * var_r)


def decay_profile(frame: PointInTimeFrame, signal: Signal, symbols: list[str],
                   sample_dates: list[dt.datetime]) -> dict[str, Optional[float]]:
    """
    For each horizon in DECAY_WINDOWS_DAYS, compute IC between the
    signal's reading at t and forward return from t to t+horizon.
    This is what tells us 'how long the effect lasts', as required.
    """
    readings = []
    for d in sample_dates:
        for sym in symbols:
            r = signal.compute(frame, sym, d)
            if r is not None:
                readings.append(r)

    results = {}
    for label, horizon_days in DECAY_WINDOWS_DAYS.items():
        forward_returns = {}
        for r in readings:
            future_time = r.timestamp + dt.timedelta(days=horizon_days)
            bar_now = frame.latest_as_of(r.symbol, r.timestamp)
            bar_future = frame.latest_as_of(r.symbol, future_time)
            if bar_now and bar_future and bar_now.close > 0:
                fwd_ret = (bar_future.close - bar_now.close) / bar_now.close
                forward_returns[(r.symbol, r.timestamp)] = fwd_ret
        results[label] = information_coefficient(readings, forward_returns)
    return results
