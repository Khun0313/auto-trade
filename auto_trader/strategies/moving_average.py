"""이동평균선 교차 전략: 정배열/역배열 판단."""

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, SignalType


class MovingAverage(BaseStrategy):
    """5일/20일/60일 이동평균선 기반 전략."""

    def __init__(self, params: dict):
        super().__init__("moving_average", params)
        self.short = params.get("short_period", 5)
        self.mid = params.get("mid_period", 20)
        self.long = params.get("long_period", 60)

    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        if len(df) < self.long + 1:
            return self._hold(stock_code)

        close = df["close"].astype(float)
        ma_s = close.rolling(self.short).mean()
        ma_m = close.rolling(self.mid).mean()
        ma_l = close.rolling(self.long).mean()

        cur_s, cur_m, cur_l = ma_s.iloc[-1], ma_m.iloc[-1], ma_l.iloc[-1]
        prev_s, prev_m = ma_s.iloc[-2], ma_m.iloc[-2]
        price = close.iloc[-1]

        confidence = self.get_confidence(stock_code, df)

        # 정배열: 5 > 20 > 60
        if cur_s > cur_m > cur_l:
            # 골든크로스 확인 (5일이 20일을 상향돌파)
            if prev_s <= prev_m and cur_s > cur_m:
                return Signal(stock_code, SignalType.BUY, 0.9, confidence,
                              self.name, "골든크로스 + 정배열")
            if price > cur_s:
                return Signal(stock_code, SignalType.BUY, 0.6, confidence,
                              self.name, "정배열 + 5MA 위")
            return Signal(stock_code, SignalType.BUY, 0.3, confidence,
                          self.name, "정배열")

        # 역배열: 5 < 20 < 60
        if cur_s < cur_m < cur_l:
            if prev_s >= prev_m and cur_s < cur_m:
                return Signal(stock_code, SignalType.SELL, -0.9, confidence,
                              self.name, "데드크로스 + 역배열")
            return Signal(stock_code, SignalType.SELL, -0.5, confidence,
                          self.name, "역배열")

        return self._hold(stock_code)

    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        if len(df) < self.long:
            return 0.3

        close = df["close"].astype(float)
        ma_m = close.rolling(self.mid).mean()

        # MA20 기울기로 추세 강도 계산
        slope = (ma_m.iloc[-1] - ma_m.iloc[-5]) / ma_m.iloc[-5] if ma_m.iloc[-5] != 0 else 0
        return min(0.4 + abs(slope) * 20, 1.0)

    def _hold(self, stock_code: str) -> Signal:
        return Signal(stock_code=stock_code, signal_type=SignalType.HOLD,
                      score=0.0, confidence=0.5, strategy_name=self.name)
