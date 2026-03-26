"""RSI 역발 전략: RSI(14) ≤30 + 볼린저밴드 하단 → 매수."""

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, SignalType


class RSIEnvelope(BaseStrategy):
    """RSI + 볼린저밴드 역추세 전략."""

    def __init__(self, params: dict):
        super().__init__("rsi_envelope", params)
        self.rsi_period = params.get("rsi_period", 14)
        self.rsi_oversold = params.get("rsi_oversold", 30)
        self.rsi_overbought = params.get("rsi_overbought", 70)
        self.bb_period = params.get("bb_period", 20)
        self.bb_std = params.get("bb_std", 2.0)

    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.rsi_period, self.bb_period) + 5:
            return self._hold(stock_code)

        close = df["close"].astype(float)
        rsi = self._calc_rsi(close)
        bb_lower, bb_upper = self._calc_bollinger(close)

        cur_rsi = rsi.iloc[-1]
        cur_price = close.iloc[-1]
        cur_bb_lower = bb_lower.iloc[-1]
        cur_bb_upper = bb_upper.iloc[-1]

        confidence = self.get_confidence(stock_code, df)

        # 과매도 + 볼린저 하단 → 매수
        if cur_rsi <= self.rsi_oversold and cur_price <= cur_bb_lower:
            score = 0.7 + (self.rsi_oversold - cur_rsi) / 100
            return Signal(stock_code, SignalType.BUY, min(score, 1.0), confidence,
                          self.name, f"RSI={cur_rsi:.0f} 과매도 + BB하단 터치")

        # RSI 반등 시작 (과매도에서 벗어나는 순간)
        if len(rsi) >= 2:
            prev_rsi = rsi.iloc[-2]
            if prev_rsi <= self.rsi_oversold and cur_rsi > self.rsi_oversold:
                return Signal(stock_code, SignalType.BUY, 0.5, confidence,
                              self.name, f"RSI 과매도 탈출 ({prev_rsi:.0f}→{cur_rsi:.0f})")

        # 과매수 + 볼린저 상단 → 매도
        if cur_rsi >= self.rsi_overbought and cur_price >= cur_bb_upper:
            score = -0.7 - (cur_rsi - self.rsi_overbought) / 100
            return Signal(stock_code, SignalType.SELL, max(score, -1.0), confidence,
                          self.name, f"RSI={cur_rsi:.0f} 과매수 + BB상단 터치")

        return self._hold(stock_code)

    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        if len(df) < self.rsi_period + 20:
            return 0.3

        close = df["close"].astype(float)
        rsi = self._calc_rsi(close)

        # RSI 극단값일수록 확신도 높음
        cur_rsi = rsi.iloc[-1]
        if cur_rsi <= 20 or cur_rsi >= 80:
            return 0.8
        if cur_rsi <= self.rsi_oversold or cur_rsi >= self.rsi_overbought:
            return 0.6
        return 0.4

    def _calc_rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(self.rsi_period).mean()
        avg_loss = loss.rolling(self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _calc_bollinger(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        ma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        upper = ma + self.bb_std * std
        lower = ma - self.bb_std * std
        return lower, upper

    def _hold(self, stock_code: str) -> Signal:
        return Signal(stock_code=stock_code, signal_type=SignalType.HOLD,
                      score=0.0, confidence=0.5, strategy_name=self.name)
