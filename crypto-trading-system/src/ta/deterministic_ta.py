"""Deterministic technical analysis driven by threshold-only YAML config."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from src.indicators.atr import calculate_atr
from src.indicators.bollinger import calculate_bollinger_bands
from src.indicators.ema import calculate_ema
from src.indicators.macd import calculate_macd
from src.indicators.rsi import calculate_rsi


def _last(values: Sequence[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return float(value)
    return None


def _simple_moving_average(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be > 0")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    for idx in range(period - 1, len(values)):
        window = values[idx - period + 1 : idx + 1]
        result[idx] = sum(window) / period
    return result


def _compute_obv(closes: list[float], volumes: list[float]) -> list[float]:
    if not closes or len(closes) != len(volumes):
        return []
    obv = [0.0]
    for idx in range(1, len(closes)):
        if closes[idx] > closes[idx - 1]:
            obv.append(obv[-1] + volumes[idx])
        elif closes[idx] < closes[idx - 1]:
            obv.append(obv[-1] - volumes[idx])
        else:
            obv.append(obv[-1])
    return obv


def _find_pivots(values: list[float], lookback: int, *, pivot_type: str) -> list[tuple[int, float]]:
    pivots: list[tuple[int, float]] = []
    if lookback <= 0 or len(values) < (lookback * 2) + 1:
        return pivots

    for idx in range(lookback, len(values) - lookback):
        center = values[idx]
        left = values[idx - lookback : idx]
        right = values[idx + 1 : idx + lookback + 1]
        if pivot_type == "high":
            if all(center > value for value in left) and all(center >= value for value in right):
                pivots.append((idx, center))
        else:
            if all(center < value for value in left) and all(center <= value for value in right):
                pivots.append((idx, center))
    return pivots


def _extract_ohlcv(ohlcv_df: Iterable[dict[str, Any]]) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    for row in ohlcv_df:
        opens.append(float(row["open"]))
        highs.append(float(row["high"]))
        lows.append(float(row["low"]))
        closes.append(float(row["close"]))
        volumes.append(float(row["volume"]))
    return opens, highs, lows, closes, volumes


def validate_ta_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    decision_cfg = dict(cfg.get("decision", {}))
    indicators_cfg = dict(cfg.get("indicators", {}))

    buy_threshold = float(decision_cfg.get("buy_threshold", 0.0))
    sell_threshold = float(decision_cfg.get("sell_threshold", 0.0))
    hold_band = float(decision_cfg.get("hold_band", 0.0))
    if not (-1.0 <= sell_threshold < -hold_band <= hold_band < buy_threshold <= 1.0):
        errors.append("decision thresholds must satisfy -1 <= sell_threshold < -hold_band <= hold_band < buy_threshold <= 1")

    ema_cfg = dict(indicators_cfg.get("ema_ma", {}))
    ema_fast = int(ema_cfg.get("ema_fast_period", 0))
    ema_slow = int(ema_cfg.get("ema_slow_period", 0))
    ma_period = int(ema_cfg.get("ma_period", 0))
    if ema_fast < 1 or ema_slow < 1 or ma_period < 1 or ema_fast >= ema_slow:
        errors.append("ema_ma periods must be >=1 and ema_fast_period < ema_slow_period")

    obv_cfg = dict(indicators_cfg.get("obv", {}))
    if int(obv_cfg.get("ma_period", 0)) < 1:
        errors.append("obv.ma_period must be >=1")

    rsi_cfg = dict(indicators_cfg.get("rsi", {}))
    rsi_period = int(rsi_cfg.get("period", 0))
    rsi_buy = float(rsi_cfg.get("buy_level", 0.0))
    rsi_sell = float(rsi_cfg.get("sell_level", 0.0))
    if rsi_period < 1 or not (0.0 <= rsi_buy < rsi_sell <= 100.0):
        errors.append("rsi config must satisfy period>=1 and 0<=buy_level<sell_level<=100")

    macd_cfg = dict(indicators_cfg.get("macd", {}))
    macd_fast = int(macd_cfg.get("fast_period", 0))
    macd_slow = int(macd_cfg.get("slow_period", 0))
    macd_signal = int(macd_cfg.get("signal_period", 0))
    if macd_fast < 1 or macd_slow < 1 or macd_signal < 1 or macd_fast >= macd_slow:
        errors.append("macd periods must be >=1 and fast_period < slow_period")

    atr_cfg = dict(indicators_cfg.get("atr", {}))
    atr_period = int(atr_cfg.get("period", 0))
    low_vol = float(atr_cfg.get("low_volatility_pct", 0.0))
    high_vol = float(atr_cfg.get("high_volatility_pct", 0.0))
    if atr_period < 1 or not (0.0 <= low_vol < high_vol):
        errors.append("atr config must satisfy period>=1 and 0<=low_volatility_pct<high_volatility_pct")

    bb_cfg = dict(indicators_cfg.get("bollinger", {}))
    bb_period = int(bb_cfg.get("period", 0))
    bb_std = float(bb_cfg.get("std_dev_mult", 0.0))
    bb_buffer = float(bb_cfg.get("touch_buffer_pct", 0.0))
    if bb_period < 1 or bb_std <= 0 or bb_buffer < 0:
        errors.append("bollinger config must satisfy period>=1, std_dev_mult>0, touch_buffer_pct>=0")

    structure_cfg = dict(indicators_cfg.get("market_structure", {}))
    if int(structure_cfg.get("pivot_lookback", 0)) < 1:
        errors.append("market_structure.pivot_lookback must be >=1")

    return errors


def compute_indicators(ohlcv_df: Sequence[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    _opens, highs, lows, closes, volumes = _extract_ohlcv(ohlcv_df)
    indicators_cfg = dict(cfg.get("indicators", {}))

    ema_cfg = dict(indicators_cfg.get("ema_ma", {}))
    ema_fast_series = calculate_ema(closes, int(ema_cfg.get("ema_fast_period", 21)))
    ema_slow_series = calculate_ema(closes, int(ema_cfg.get("ema_slow_period", 50)))
    ma_series = _simple_moving_average(closes, int(ema_cfg.get("ma_period", 200)))

    obv_cfg = dict(indicators_cfg.get("obv", {}))
    obv_series = _compute_obv(closes, volumes)
    obv_ma_series = _simple_moving_average(obv_series, int(obv_cfg.get("ma_period", 20)))

    rsi_cfg = dict(indicators_cfg.get("rsi", {}))
    rsi_series = calculate_rsi(closes, int(rsi_cfg.get("period", 14)))

    macd_cfg = dict(indicators_cfg.get("macd", {}))
    macd = calculate_macd(
        closes,
        fast_period=int(macd_cfg.get("fast_period", 12)),
        slow_period=int(macd_cfg.get("slow_period", 26)),
        signal_period=int(macd_cfg.get("signal_period", 9)),
    )

    atr_cfg = dict(indicators_cfg.get("atr", {}))
    atr_series = calculate_atr(highs, lows, closes, int(atr_cfg.get("period", 14)))
    atr_value = _last(atr_series)
    atr_pct = (atr_value / closes[-1]) if atr_value is not None and closes and closes[-1] > 0 else None

    bb_cfg = dict(indicators_cfg.get("bollinger", {}))
    bollinger = calculate_bollinger_bands(
        closes,
        period=int(bb_cfg.get("period", 20)),
        std_dev_mult=float(bb_cfg.get("std_dev_mult", 2.0)),
    )

    structure_cfg = dict(indicators_cfg.get("market_structure", {}))
    pivot_lookback = int(structure_cfg.get("pivot_lookback", 3))
    pivot_highs = _find_pivots(highs, pivot_lookback, pivot_type="high")
    pivot_lows = _find_pivots(lows, pivot_lookback, pivot_type="low")
    trend_state = "neutral"
    if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
        prev_high = pivot_highs[-2][1]
        last_high = pivot_highs[-1][1]
        prev_low = pivot_lows[-2][1]
        last_low = pivot_lows[-1][1]
        if last_high > prev_high and last_low > prev_low:
            trend_state = "bullish"
        elif last_high < prev_high and last_low < prev_low:
            trend_state = "bearish"

    return {
        "ema_ma": {
            "close": closes[-1] if closes else None,
            "ema_fast": _last(ema_fast_series),
            "ema_slow": _last(ema_slow_series),
            "ma": _last(ma_series),
        },
        "obv": {
            "obv": obv_series[-1] if obv_series else None,
            "obv_ma": _last(obv_ma_series),
        },
        "rsi": {
            "rsi": _last(rsi_series),
        },
        "macd": {
            "macd": _last(macd["macd"]),
            "signal": _last(macd["signal"]),
            "histogram": _last(macd["histogram"]),
        },
        "atr": {
            "atr": atr_value,
            "atr_pct": atr_pct,
        },
        "bollinger": {
            "close": closes[-1] if closes else None,
            "middle": _last(bollinger["middle"]),
            "upper": _last(bollinger["upper"]),
            "lower": _last(bollinger["lower"]),
        },
        "market_structure": {
            "trend_state": trend_state,
            "last_pivot_high": pivot_highs[-1][1] if pivot_highs else None,
            "last_pivot_low": pivot_lows[-1][1] if pivot_lows else None,
            "pivot_high_count": len(pivot_highs),
            "pivot_low_count": len(pivot_lows),
        },
    }


def compute_signals(indicators: dict[str, Any], ohlcv_df: Sequence[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _opens, _highs, _lows, closes, _volumes = _extract_ohlcv(ohlcv_df)
    indicators_cfg = dict(cfg.get("indicators", {}))
    signals: dict[str, dict[str, Any]] = {}

    ema_cfg = dict(indicators_cfg.get("ema_ma", {}))
    ema_values = dict(indicators.get("ema_ma", {}))
    ema_signal = 0
    ema_fast = ema_values.get("ema_fast")
    ema_slow = ema_values.get("ema_slow")
    ma_value = ema_values.get("ma")
    close_value = ema_values.get("close")
    if all(value is not None for value in (ema_fast, ema_slow, ma_value, close_value)):
        min_gap_pct = float(ema_cfg.get("min_trend_gap_pct", 0.0))
        gap_pct = abs(float(ema_fast) - float(ema_slow)) / max(abs(float(close_value)), 1e-9)
        if float(close_value) > float(ema_fast) > float(ema_slow) > float(ma_value) and gap_pct >= min_gap_pct:
            ema_signal = 1
        elif float(close_value) < float(ema_fast) < float(ema_slow) < float(ma_value) and gap_pct >= min_gap_pct:
            ema_signal = -1
    signals["ema_ma"] = {
        "enabled": bool(ema_cfg.get("enabled", True)),
        "signal": ema_signal,
        "strength": abs((float(ema_fast or 0.0) - float(ema_slow or 0.0)) / max(abs(float(close_value or 1.0)), 1e-9)),
    }

    obv_cfg = dict(indicators_cfg.get("obv", {}))
    obv_values = dict(indicators.get("obv", {}))
    obv_signal = 0
    obv_value = obv_values.get("obv")
    obv_ma = obv_values.get("obv_ma")
    if obv_value is not None and obv_ma is not None:
        min_delta_ratio = float(obv_cfg.get("min_delta_ratio", 0.0))
        baseline = max(abs(float(obv_ma)), 1.0)
        delta_ratio = (float(obv_value) - float(obv_ma)) / baseline
        if delta_ratio > min_delta_ratio:
            obv_signal = 1
        elif delta_ratio < -min_delta_ratio:
            obv_signal = -1
    signals["obv"] = {
        "enabled": bool(obv_cfg.get("enabled", True)),
        "signal": obv_signal,
        "strength": abs((float(obv_value or 0.0) - float(obv_ma or 0.0)) / max(abs(float(obv_ma or 1.0)), 1.0)),
    }

    rsi_cfg = dict(indicators_cfg.get("rsi", {}))
    rsi_value = dict(indicators.get("rsi", {})).get("rsi")
    rsi_signal = 0
    if rsi_value is not None:
        if float(rsi_value) <= float(rsi_cfg.get("buy_level", 35.0)):
            rsi_signal = 1
        elif float(rsi_value) >= float(rsi_cfg.get("sell_level", 65.0)):
            rsi_signal = -1
    signals["rsi"] = {
        "enabled": bool(rsi_cfg.get("enabled", True)),
        "signal": rsi_signal,
        "strength": abs((float(rsi_value or 50.0) - 50.0) / 50.0),
    }

    macd_cfg = dict(indicators_cfg.get("macd", {}))
    macd_values = dict(indicators.get("macd", {}))
    macd_value = macd_values.get("macd")
    macd_signal_value = macd_values.get("signal")
    macd_hist = macd_values.get("histogram")
    macd_signal = 0
    hist_epsilon = float(macd_cfg.get("histogram_epsilon", 0.0))
    if macd_value is not None and macd_signal_value is not None and macd_hist is not None:
        if float(macd_value) > float(macd_signal_value) and float(macd_hist) > hist_epsilon:
            macd_signal = 1
        elif float(macd_value) < float(macd_signal_value) and float(macd_hist) < (-1.0 * hist_epsilon):
            macd_signal = -1
    signals["macd"] = {
        "enabled": bool(macd_cfg.get("enabled", True)),
        "signal": macd_signal,
        "strength": abs(float(macd_hist or 0.0)),
    }

    atr_cfg = dict(indicators_cfg.get("atr", {}))
    atr_values = dict(indicators.get("atr", {}))
    atr_pct = atr_values.get("atr_pct")
    atr_signal = 0
    if atr_pct is not None:
        if float(atr_pct) <= float(atr_cfg.get("low_volatility_pct", 0.01)):
            atr_signal = 1
        elif float(atr_pct) >= float(atr_cfg.get("high_volatility_pct", 0.03)):
            atr_signal = -1
    signals["atr"] = {
        "enabled": bool(atr_cfg.get("enabled", True)),
        "signal": atr_signal,
        "strength": abs(float(atr_pct or 0.0)),
    }

    bb_cfg = dict(indicators_cfg.get("bollinger", {}))
    bb_values = dict(indicators.get("bollinger", {}))
    close_value = bb_values.get("close")
    lower_band = bb_values.get("lower")
    upper_band = bb_values.get("upper")
    bb_signal = 0
    if close_value is not None and lower_band is not None and upper_band is not None:
        buffer_pct = float(bb_cfg.get("touch_buffer_pct", 0.01))
        if float(close_value) <= float(lower_band) * (1.0 + buffer_pct):
            bb_signal = 1
        elif float(close_value) >= float(upper_band) * (1.0 - buffer_pct):
            bb_signal = -1
    signals["bollinger"] = {
        "enabled": bool(bb_cfg.get("enabled", True)),
        "signal": bb_signal,
        "strength": abs(
            (float(close_value or 0.0) - float(bb_values.get("middle") or close_value or 0.0))
            / max(abs(float(close_value or 1.0)), 1e-9)
        ),
    }

    structure_cfg = dict(indicators_cfg.get("market_structure", {}))
    structure_values = dict(indicators.get("market_structure", {}))
    trend_state = str(structure_values.get("trend_state", "neutral"))
    structure_signal = 1 if trend_state == "bullish" else -1 if trend_state == "bearish" else 0
    signals["market_structure"] = {
        "enabled": bool(structure_cfg.get("enabled", True)),
        "signal": structure_signal,
        "strength": 1.0 if structure_signal != 0 else 0.0,
    }

    return signals


def combine_vote_score(signals: dict[str, dict[str, Any]], cfg: dict[str, Any]) -> tuple[float, float, dict[str, Any]]:
    enabled_signals = {
        name: int(payload.get("signal", 0))
        for name, payload in signals.items()
        if bool(payload.get("enabled", True))
    }
    raw = sum(enabled_signals.values())
    count = max(1, len(enabled_signals))
    score = raw / count
    confidence = abs(score)
    decision_cfg = dict(cfg.get("decision", {}))
    breakdown = {
        "enabled_indicators": list(enabled_signals.keys()),
        "per_indicator_signal": enabled_signals,
        "raw_sum": raw,
        "count": len(enabled_signals),
        "thresholds": {
            "buy_threshold": float(decision_cfg.get("buy_threshold", 0.34)),
            "sell_threshold": float(decision_cfg.get("sell_threshold", -0.34)),
            "hold_band": float(decision_cfg.get("hold_band", 0.15)),
        },
    }
    return score, confidence, breakdown
