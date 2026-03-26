"""주간 전략 평가: 승률, Sharpe, MDD, Profit Factor."""

from analysis.performance_tracker import PerformanceTracker
from data.db.repository import insert_weekly_evaluation
from utils.logger import get_logger

logger = get_logger("strategy_evaluator")


class StrategyEvaluator:
    """주간 전략 평가기."""

    def __init__(self, tracker: PerformanceTracker):
        self.tracker = tracker

    def evaluate_all(self, strategies: list[str],
                     week_start: str, week_end: str) -> list[dict]:
        """모든 전략의 주간 성과를 평가한다."""
        results = []
        for strategy in strategies:
            perf = self.tracker.get_strategy_performance(strategy, days=7)

            insert_weekly_evaluation(
                week_start=week_start,
                week_end=week_end,
                strategy=strategy,
                sharpe_ratio=0,  # 일주일 데이터로는 의미 없음, 백테스트에서 계산
                max_drawdown=perf.get("max_drawdown", 0),
                profit_factor=perf.get("profit_factor", 0),
                win_rate=perf.get("win_rate", 0),
                total_trades=perf.get("trades", 0),
            )

            results.append(perf)
            logger.info(
                "전략 %s 주간: 거래=%d, 승률=%.0f%%, PF=%.2f, MDD=%.1f%%",
                strategy, perf.get("trades", 0), perf.get("win_rate", 0),
                perf.get("profit_factor", 0), perf.get("max_drawdown", 0),
            )

        return results
