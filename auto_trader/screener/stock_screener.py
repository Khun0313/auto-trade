"""2단계 종목 스크리너: 1차(기본 필터) → 2차(기술적 필터)."""

import pandas as pd
import yaml
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("screener")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class StockScreener:
    """2단계 종목 스크리너."""

    def __init__(self):
        self._load_blacklist()

    def _load_blacklist(self):
        with open(CONFIG_DIR / "watchlist.yaml", "r", encoding="utf-8") as f:
            wl = yaml.safe_load(f)
        self.blacklist = set(item.get("code", "") for item in wl.get("blacklist", []))

    def screen_phase1(self, candidates: list[dict]) -> list[dict]:
        """1차 필터링: 시총, 거래량, 블랙리스트.

        Args:
            candidates: [{"code", "name", "market_cap", "volume", "avg_volume_20"}]

        Returns:
            통과한 종목 리스트.
        """
        passed = []
        for stock in candidates:
            code = stock.get("code", "")

            # 블랙리스트 체크
            if code in self.blacklist:
                continue

            # 시총 500억 이상
            market_cap = stock.get("market_cap", 0)
            if market_cap < 50_000_000_000:
                continue

            # 20일 평균 거래량 10만주 이상
            avg_vol = stock.get("avg_volume_20", 0)
            if avg_vol < 100_000:
                continue

            passed.append(stock)

        logger.info("1차 스크리닝: %d → %d 종목", len(candidates), len(passed))
        return passed

    def screen_phase2(self, candidates: list[dict], prices_map: dict[str, pd.DataFrame]) -> list[dict]:
        """2차 필터링: MA, RSI, 수급, ATR.

        Args:
            candidates: 1차 통과 종목.
            prices_map: {종목코드: OHLCV 데이터프레임}.

        Returns:
            최종 통과 종목 (점수 내림차순).
        """
        scored = []
        for stock in candidates:
            code = stock["code"]
            df = prices_map.get(code)
            if df is None or len(df) < 60:
                continue

            close = df["close"].astype(float)
            score = 0

            # MA20 위: +1
            ma20 = close.rolling(20).mean().iloc[-1]
            if close.iloc[-1] > ma20:
                score += 1

            # RSI(14) 30~70 범위: +1
            rsi = self._calc_rsi(close, 14)
            if 30 <= rsi <= 70:
                score += 1

            # 거래량 증가 (20일 평균 대비): +1
            vol = df["volume"].astype(float)
            avg_vol = vol.rolling(20).mean().iloc[-1]
            if vol.iloc[-1] > avg_vol * 1.2:
                score += 1

            # ATR 적정 범위 (1~5%): +1
            atr = self._calc_atr(df, 14)
            atr_pct = atr / close.iloc[-1] * 100
            if 1.0 <= atr_pct <= 5.0:
                score += 1

            stock["screen_score"] = score
            scored.append(stock)

        # 점수 내림차순 정렬
        scored.sort(key=lambda x: x["screen_score"], reverse=True)
        logger.info("2차 스크리닝: %d → %d 종목 (점수 기준)", len(candidates), len(scored))
        return scored

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean().iloc[-1]
        loss = (-delta).clip(lower=0).rolling(period).mean().iloc[-1]
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]
