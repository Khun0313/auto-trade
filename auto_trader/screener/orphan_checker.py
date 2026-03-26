"""고아 종목 체커: 보유기간/전략미적용/수익률정체/최대보유 체크."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from utils.logger import get_logger

logger = get_logger("orphan_checker")


@dataclass
class OrphanResult:
    stock_code: str
    stock_name: str
    reason: str
    holding_days: int
    pnl_pct: float


class OrphanChecker:
    """고아 종목(방치된 보유종목)을 탐지한다."""

    def __init__(self, max_holding_days: int = 10, stagnant_pnl_pct: float = 1.0):
        self.max_holding_days = max_holding_days
        self.stagnant_pnl_pct = stagnant_pnl_pct

    def check(self, positions: list[dict], active_strategies: list[str]) -> list[OrphanResult]:
        """보유 종목 중 고아를 탐지한다.

        Args:
            positions: [{"code", "name", "buy_date", "buy_price", "current_price", "strategy"}]
            active_strategies: 현재 활성 전략 이름 리스트.

        Returns:
            OrphanResult 리스트.
        """
        orphans = []
        now = datetime.now()

        for pos in positions:
            code = pos["code"]
            name = pos.get("name", code)
            buy_date = datetime.fromisoformat(pos["buy_date"])
            holding_days = (now - buy_date).days
            pnl_pct = ((pos["current_price"] - pos["buy_price"]) / pos["buy_price"]) * 100

            # 최대 보유기간 초과
            if holding_days > self.max_holding_days:
                orphans.append(OrphanResult(
                    code, name, f"보유기간 초과 ({holding_days}일)", holding_days, pnl_pct,
                ))
                continue

            # 전략 미적용 (원래 전략이 비활성화됨)
            strategy = pos.get("strategy", "")
            if strategy and strategy not in active_strategies:
                orphans.append(OrphanResult(
                    code, name, f"전략 비활성 ({strategy})", holding_days, pnl_pct,
                ))
                continue

            # 수익률 정체 (3일 이상 보유 + 수익률 ±1% 이내)
            if holding_days >= 3 and abs(pnl_pct) < self.stagnant_pnl_pct:
                orphans.append(OrphanResult(
                    code, name, f"수익률 정체 ({pnl_pct:+.1f}%)", holding_days, pnl_pct,
                ))

        if orphans:
            logger.warning("고아 종목 %d건 탐지: %s",
                           len(orphans), [o.stock_code for o in orphans])
        return orphans
