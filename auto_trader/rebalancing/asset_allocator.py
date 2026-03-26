"""자산 배분기: 장세별 목표 비중, 편차 허용, 점진적 이동."""

from dataclasses import dataclass

from analysis.market_regime import MarketRegime
from utils.logger import get_logger

logger = get_logger("asset_allocator")


# 장세별 목표 자산 배분 (%)
TARGET_ALLOCATION = {
    MarketRegime.STRONG_BULL: {"stock": 70, "etf": 10, "cash": 20},
    MarketRegime.WEAK_BULL:   {"stock": 55, "etf": 20, "cash": 25},
    MarketRegime.SIDEWAYS:    {"stock": 40, "etf": 30, "cash": 30},
    MarketRegime.WEAK_BEAR:   {"stock": 20, "etf": 40, "cash": 40},
    MarketRegime.STRONG_BEAR: {"stock": 5,  "etf": 35, "cash": 60},
}

TOLERANCE_PCT = 5   # 허용 편차 (%)
MOVE_RATIO = 0.5    # 점진적 이동 비율 (목표와의 차이의 50%만 이동)


@dataclass
class AllocationPlan:
    """리밸런싱 계획."""
    regime: MarketRegime
    current: dict[str, float]   # {"stock": %, "etf": %, "cash": %}
    target: dict[str, float]
    adjustments: dict[str, float]  # 조정량 (양수=매수, 음수=매도)
    needs_rebalance: bool


class AssetAllocator:
    """장세 기반 자산 배분기."""

    def plan_rebalance(self, regime: MarketRegime,
                       total_value: float,
                       stock_value: float,
                       etf_value: float,
                       cash_value: float) -> AllocationPlan:
        """리밸런싱 계획을 생성한다.

        Args:
            regime: 현재 장세.
            total_value: 총 자산.
            stock_value: 주식 평가금.
            etf_value: ETF 평가금.
            cash_value: 현금.
        """
        if total_value <= 0:
            return AllocationPlan(regime, {}, {}, {}, False)

        current = {
            "stock": (stock_value / total_value) * 100,
            "etf": (etf_value / total_value) * 100,
            "cash": (cash_value / total_value) * 100,
        }
        target = TARGET_ALLOCATION.get(regime, TARGET_ALLOCATION[MarketRegime.SIDEWAYS])

        # 편차 확인
        needs_rebalance = False
        adjustments = {}
        for asset_type in ["stock", "etf", "cash"]:
            diff = target[asset_type] - current[asset_type]
            if abs(diff) > TOLERANCE_PCT:
                needs_rebalance = True
                # 점진적 이동 (50%)
                adjustments[asset_type] = diff * MOVE_RATIO
            else:
                adjustments[asset_type] = 0

        if needs_rebalance:
            logger.info(
                "리밸런싱 필요 (장세: %s) 현재: %s → 목표: %s → 조정: %s",
                regime.value,
                {k: f"{v:.1f}%" for k, v in current.items()},
                {k: f"{v}%" for k, v in target.items()},
                {k: f"{v:+.1f}%" for k, v in adjustments.items() if v != 0},
            )

        return AllocationPlan(
            regime=regime,
            current=current,
            target=target,
            adjustments=adjustments,
            needs_rebalance=needs_rebalance,
        )
