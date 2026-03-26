"""변동성 돌파 전략: 전일 Range × K배 돌파 시 매수, 당일 종가 매도."""

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, SignalType


class VolatilityBreakout(BaseStrategy):
    """래리 윌리엄스 변동성 돌파 전략."""

    def __init__(self, params: dict):
        super().__init__("volatility_breakout", params)
        self.k_value = params.get("k_value", 0.5)
        self.volume_threshold = params.get("volume_threshold", 1.5)

    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        if len(df) < 3:
            return self._hold(stock_code)

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        # 전일 Range
        prev_range = float(yesterday["high"]) - float(yesterday["low"])
        breakout_price = float(today["open"]) + prev_range * self.k_value

        current_price = float(today["close"])
        current_volume = float(today["volume"])
        avg_volume = float(df["volume"].tail(20).mean())

        # 돌파 + 거래량 확인
        if current_price > breakout_price and current_volume > avg_volume * self.volume_threshold:
            strength = min((current_price - breakout_price) / prev_range, 1.0) if prev_range > 0 else 0.5
            confidence = self.get_confidence(stock_code, df)
            return Signal(
                stock_code=stock_code,
                signal_type=SignalType.BUY,
                score=0.5 + strength * 0.5,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"돌파가 {breakout_price:.0f} 상향 돌파 (거래량 {current_volume/avg_volume:.1f}배)",
            )

        return self._hold(stock_code)

    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        if len(df) < 20:
            return 0.3

        # 최근 20일 변동성 돌파 성공률로 확신도 계산
        success = 0
        total = 0
        for i in range(max(2, len(df) - 20), len(df) - 1):
            prev_range = float(df.iloc[i - 1]["high"]) - float(df.iloc[i - 1]["low"])
            target = float(df.iloc[i]["open"]) + prev_range * self.k_value
            if float(df.iloc[i]["high"]) > target:
                total += 1
                if float(df.iloc[i]["close"]) > target:
                    success += 1

        if total == 0:
            return 0.4
        return min(0.3 + (success / total) * 0.7, 1.0)

    def _hold(self, stock_code: str) -> Signal:
        return Signal(stock_code=stock_code, signal_type=SignalType.HOLD,
                      score=0.0, confidence=0.5, strategy_name=self.name)
