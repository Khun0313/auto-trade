"""활성 감시 목록 관리: 보유종목(필수) + 고정 ETF + 스크리닝 후보."""

import yaml
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("watchlist")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class WatchlistManager:
    """감시 목록 관리자 — WebSocket 구독 우선순위를 결정한다."""

    MAX_WS_SUBSCRIPTIONS = 41

    def __init__(self):
        self.held_stocks: list[str] = []       # 보유 종목 (최우선)
        self.buy_candidates: list[str] = []     # 매수 후보
        self.etf_codes: list[str] = []          # 고정 ETF
        self.manual_codes: list[str] = []       # 수동 관심종목
        self._load_config()

    def _load_config(self):
        with open(CONFIG_DIR / "watchlist.yaml", "r", encoding="utf-8") as f:
            wl = yaml.safe_load(f)

        self.etf_codes = [item["code"] for item in wl.get("etf", [])]
        self.manual_codes = [item["code"] for item in wl.get("manual", [])]

    def update_held_stocks(self, codes: list[str]):
        """보유 종목을 업데이트한다."""
        self.held_stocks = codes
        logger.debug("보유 종목 업데이트: %d개", len(codes))

    def update_buy_candidates(self, codes: list[str]):
        """매수 후보를 업데이트한다."""
        self.buy_candidates = codes
        logger.debug("매수 후보 업데이트: %d개", len(codes))

    def get_active_watchlist(self) -> list[str]:
        """우선순위 기반 활성 감시 목록을 반환한다.

        우선순위: 보유 > 매수후보 > 수동 관심 > ETF
        최대 41종목 (WebSocket 제한).
        """
        seen = set()
        result = []

        for codes in [self.held_stocks, self.buy_candidates,
                      self.manual_codes, self.etf_codes]:
            for code in codes:
                if code not in seen and len(result) < self.MAX_WS_SUBSCRIPTIONS:
                    seen.add(code)
                    result.append(code)

        logger.info(
            "활성 감시목록: %d종목 (보유=%d, 후보=%d, 수동=%d, ETF=%d)",
            len(result), len(self.held_stocks), len(self.buy_candidates),
            len(self.manual_codes), len(self.etf_codes),
        )
        return result
