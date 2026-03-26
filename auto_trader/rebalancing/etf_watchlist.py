"""ETF 종목 관리: 8개 고정 ETF 유니버스."""

import yaml
from pathlib import Path

from analysis.market_regime import MarketRegime
from utils.logger import get_logger

logger = get_logger("etf_watchlist")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# 장세별 선호 ETF 카테고리
REGIME_ETF_PREFERENCE = {
    MarketRegime.STRONG_BULL: ["kospi"],
    MarketRegime.WEAK_BULL:   ["kospi", "gold"],
    MarketRegime.SIDEWAYS:    ["gold", "bond", "dollar"],
    MarketRegime.WEAK_BEAR:   ["kospi_inverse", "gold", "bond", "dollar"],
    MarketRegime.STRONG_BEAR: ["kospi_inverse", "bond", "gold", "dollar"],
}


class ETFWatchlist:
    """ETF 유니버스 관리."""

    def __init__(self):
        self.etfs: list[dict] = []
        self._load()

    def _load(self):
        with open(CONFIG_DIR / "watchlist.yaml", "r", encoding="utf-8") as f:
            wl = yaml.safe_load(f)
        self.etfs = wl.get("etf", [])

    def get_preferred_etfs(self, regime: MarketRegime) -> list[dict]:
        """장세에 맞는 선호 ETF를 반환한다."""
        preferred_cats = REGIME_ETF_PREFERENCE.get(regime, [])
        result = [etf for etf in self.etfs if etf.get("category") in preferred_cats]
        logger.debug("장세 %s 선호 ETF: %s", regime.value, [e["name"] for e in result])
        return result

    def get_all_codes(self) -> list[str]:
        """모든 ETF 코드를 반환한다."""
        return [etf["code"] for etf in self.etfs]
