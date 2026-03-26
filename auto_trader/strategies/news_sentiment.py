"""뉴스 감성 전략: AI 감성점수 + 기술적 확인."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, SignalType
from utils.logger import get_logger

logger = get_logger("news_sentiment")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "auto_trader.db"


class NewsSentiment(BaseStrategy):
    """뉴스 감성 분석 기반 전략."""

    def __init__(self, params: dict):
        super().__init__("news_sentiment", params)
        self.min_score = params.get("min_score", 0.3)
        self.tech_confirm = params.get("tech_confirm", True)
        self.max_age_hours = params.get("max_news_age_hours", 4)

    def generate_signal(self, stock_code: str, df: pd.DataFrame) -> Signal:
        # 최근 뉴스 감성 점수 조회
        sentiment = self._get_recent_sentiment(stock_code)
        if sentiment is None:
            return self._hold(stock_code)

        avg_score, news_count = sentiment
        confidence = self.get_confidence(stock_code, df)

        # 기술적 확인 (20일 MA 위/아래)
        tech_ok = True
        if self.tech_confirm and len(df) >= 20:
            close = df["close"].astype(float)
            ma20 = close.rolling(20).mean().iloc[-1]
            price = close.iloc[-1]
            if avg_score > 0 and price < ma20:
                tech_ok = False
            elif avg_score < 0 and price > ma20:
                tech_ok = False

        if abs(avg_score) < self.min_score:
            return self._hold(stock_code)

        if not tech_ok:
            # 기술적 확인 실패 시 점수 감소
            avg_score *= 0.3

        if avg_score > 0:
            return Signal(stock_code, SignalType.BUY, min(avg_score, 1.0), confidence,
                          self.name, f"긍정 뉴스 {news_count}건 (감성={avg_score:.2f})")
        else:
            return Signal(stock_code, SignalType.SELL, max(avg_score, -1.0), confidence,
                          self.name, f"부정 뉴스 {news_count}건 (감성={avg_score:.2f})")

    def get_confidence(self, stock_code: str, df: pd.DataFrame) -> float:
        sentiment = self._get_recent_sentiment(stock_code)
        if sentiment is None:
            return 0.2

        _, news_count = sentiment
        # 뉴스 수에 비례하여 확신도 증가
        return min(0.3 + news_count * 0.1, 0.8)

    def _get_recent_sentiment(self, stock_code: str) -> tuple[float, int] | None:
        """최근 N시간 내 해당 종목 뉴스의 평균 감성 점수를 조회한다."""
        cutoff = (datetime.now() - timedelta(hours=self.max_age_hours)).isoformat()

        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.execute(
                """SELECT AVG(sentiment_score), COUNT(*)
                   FROM news
                   WHERE stock_codes LIKE ? AND sentiment_score IS NOT NULL
                     AND collected_at >= ?""",
                (f'%"{stock_code}"%', cutoff),
            )
            row = cursor.fetchone()
            conn.close()

            if row and row[1] > 0:
                return (row[0], row[1])
        except Exception as e:
            logger.warning("뉴스 감성 조회 오류: %s", e)

        return None

    def _hold(self, stock_code: str) -> Signal:
        return Signal(stock_code=stock_code, signal_type=SignalType.HOLD,
                      score=0.0, confidence=0.3, strategy_name=self.name)
