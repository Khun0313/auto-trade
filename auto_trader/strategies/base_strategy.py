"""전략 추상 클래스."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """전략 신호."""
    stock_code: str
    signal_type: SignalType
    score: float          # -1.0 ~ +1.0
    confidence: float     # 0.0 ~ 1.0
    strategy_name: str
    reason: str = ""


class BaseStrategy(ABC):
    """모든 매매 전략의 기반 클래스."""

    def __init__(self, name: str, params: dict):
        self.name = name
        self.params = params
        self.enabled = params.get("enabled", True)

    @abstractmethod
    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        """주어진 데이터로 매매 신호를 생성한다.

        Args:
            stock_code: 종목 코드.
            df: OHLCV 데이터프레임 (오름차순).

        Returns:
            Signal 객체.
        """

    @abstractmethod
    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        """신호의 확신도를 계산한다 (0.0 ~ 1.0)."""

    def get_parameters(self) -> dict:
        """현재 전략 파라미터를 반환한다."""
        return self.params.copy()

    def update_parameters(self, new_params: dict):
        """전략 파라미터를 업데이트한다."""
        self.params.update(new_params)

    def backtest(self, df: pd.DataFrame) -> dict:
        """간이 백테스트를 실행한다. 서브 클래스에서 오버라이드 가능."""
        signals = []
        for i in range(60, len(df)):
            window = df.iloc[:i + 1]
            sig = self.generate_signal("BACKTEST", window)
            signals.append(sig)
        return {"signals": signals, "count": len(signals)}
