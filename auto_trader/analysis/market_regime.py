"""장세 판단 모듈: KOSPI 기반 5단계 시장 국면 분류."""

from enum import Enum

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger("market_regime")


class MarketRegime(Enum):
    STRONG_BULL = "strong_bull"
    WEAK_BULL = "weak_bull"
    SIDEWAYS = "sideways"
    WEAK_BEAR = "weak_bear"
    STRONG_BEAR = "strong_bear"


class MarketRegimeClassifier:
    """KOSPI 지수 기반 시장 국면 분류기.

    판단 기준:
    - MA 배열 (5일, 20일, 60일)
    - ADR (등락비율)
    - 변동폭 (ATR)
    """

    def __init__(self):
        self.current_regime = MarketRegime.SIDEWAYS
        self._history: list[MarketRegime] = []

    def classify(self, kospi_df: pd.DataFrame) -> MarketRegime:
        """KOSPI 일봉 데이터로 장세를 판단한다.

        Args:
            kospi_df: 컬럼 - close, high, low, volume. 인덱스 - 날짜 (오름차순).

        Returns:
            MarketRegime 열거형.
        """
        if len(kospi_df) < 60:
            logger.warning("데이터 부족 (%d일). 횡보로 판단.", len(kospi_df))
            return MarketRegime.SIDEWAYS

        close = kospi_df["close"].astype(float)

        # 이동평균
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()

        latest_close = close.iloc[-1]
        latest_ma5 = ma5.iloc[-1]
        latest_ma20 = ma20.iloc[-1]
        latest_ma60 = ma60.iloc[-1]

        # MA 배열 점수 (-2 ~ +2)
        ma_score = 0
        if latest_ma5 > latest_ma20 > latest_ma60:
            ma_score = 2  # 정배열
        elif latest_ma5 > latest_ma20:
            ma_score = 1
        elif latest_ma5 < latest_ma20 < latest_ma60:
            ma_score = -2  # 역배열
        elif latest_ma5 < latest_ma20:
            ma_score = -1

        # 현재가 위치 점수
        price_score = 0
        if latest_close > latest_ma20:
            price_score += 1
        else:
            price_score -= 1
        if latest_close > latest_ma60:
            price_score += 1
        else:
            price_score -= 1

        # 20일 모멘텀
        momentum = (latest_close / close.iloc[-20] - 1) * 100 if len(close) >= 20 else 0

        # ADR (20일 등락비율)
        adr = self._calc_adr(kospi_df)

        # 변동성 (ATR 20일 / 종가 비율)
        atr_pct = self._calc_atr_pct(kospi_df)

        # 종합 점수 계산
        total_score = (
            ma_score * 1.5
            + price_score * 1.0
            + np.clip(momentum / 5, -2, 2)
            + np.clip((adr - 100) / 20, -1, 1)
        )

        # 5단계 분류
        if total_score >= 4:
            regime = MarketRegime.STRONG_BULL
        elif total_score >= 1.5:
            regime = MarketRegime.WEAK_BULL
        elif total_score >= -1.5:
            regime = MarketRegime.SIDEWAYS
        elif total_score >= -4:
            regime = MarketRegime.WEAK_BEAR
        else:
            regime = MarketRegime.STRONG_BEAR

        self.current_regime = regime
        self._history.append(regime)

        logger.info(
            "장세 판단: %s (점수: %.1f, MA: %d, 가격: %d, 모멘텀: %.1f%%, ADR: %.0f, ATR%%: %.2f%%)",
            regime.value, total_score, ma_score, price_score, momentum, adr, atr_pct,
        )
        return regime

    def _calc_adr(self, df: pd.DataFrame, period: int = 20) -> float:
        """등락비율(ADR)을 계산한다."""
        if len(df) < period + 1:
            return 100.0

        changes = df["close"].astype(float).diff().tail(period)
        advances = (changes > 0).sum()
        declines = (changes < 0).sum()

        if declines == 0:
            return 200.0
        return (advances / declines) * 100

    def _calc_atr_pct(self, df: pd.DataFrame, period: int = 20) -> float:
        """ATR을 종가 대비 퍼센트로 계산한다."""
        if len(df) < period + 1:
            return 0.0

        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(period).mean().iloc[-1]
        return (atr / close.iloc[-1]) * 100
