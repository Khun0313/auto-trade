"""엔벨로프 전략: MA20 ± N% 밴드 터치 후 반등 감지."""

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, SignalType


class Envelope(BaseStrategy):
    """이동평균 엔벨로프 기반 평균회귀 전략."""

    def __init__(self, params: dict):
        super().__init__("envelope", params)
        self.ma_period = params.get("ma_period", 20)
        self.upper_pct = params.get("upper_pct", 5.0) / 100
        self.lower_pct = params.get("lower_pct", -5.0) / 100

    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        if len(df) < self.ma_period + 5:
            return self._hold(stock_code)

        close = df["close"].astype(float)
        ma = close.rolling(self.ma_period).mean()

        cur_price = close.iloc[-1]
        prev_price = close.iloc[-2]
        cur_ma = ma.iloc[-1]

        upper_band = cur_ma * (1 + self.upper_pct)
        lower_band = cur_ma * (1 + self.lower_pct)

        confidence = self.get_confidence(stock_code, df)

        # 하단 밴드 터치 후 반등
        prev_lower = ma.iloc[-2] * (1 + self.lower_pct)
        if prev_price <= prev_lower and cur_price > lower_band:
            deviation = (cur_ma - cur_price) / cur_ma
            score = min(0.5 + deviation * 5, 1.0)
            return Signal(stock_code, SignalType.BUY, score, confidence,
                          self.name, f"하단밴드 반등 (MA{self.ma_period} {self.lower_pct*100:.0f}%)")

        # 하단 밴드 아래에 있으면서 양봉
        if cur_price < lower_band and cur_price > float(df.iloc[-1]["open"]):
            return Signal(stock_code, SignalType.BUY, 0.4, confidence,
                          self.name, "하단밴드 아래 양봉")

        # 상단 밴드 터치 후 반락
        prev_upper = ma.iloc[-2] * (1 + self.upper_pct)
        if prev_price >= prev_upper and cur_price < upper_band:
            deviation = (cur_price - cur_ma) / cur_ma
            score = max(-0.5 - deviation * 5, -1.0)
            return Signal(stock_code, SignalType.SELL, score, confidence,
                          self.name, f"상단밴드 반락 (MA{self.ma_period} +{self.upper_pct*100:.0f}%)")

        return self._hold(stock_code)

    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        if len(df) < self.ma_period + 20:
            return 0.3

        close = df["close"].astype(float)
        ma = close.rolling(self.ma_period).mean()

        # MA와의 괴리율이 클수록 확신도 높음
        deviation = abs(close.iloc[-1] - ma.iloc[-1]) / ma.iloc[-1]
        return min(0.3 + deviation * 10, 0.9)

    def _hold(self, stock_code: str) -> Signal:
        return Signal(stock_code=stock_code, signal_type=SignalType.HOLD,
                      score=0.0, confidence=0.5, strategy_name=self.name)
