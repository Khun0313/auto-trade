"""일일 AI 평가: 15:40 자동 실행."""

from datetime import date

from analysis.performance_tracker import PerformanceTracker
from data.db.repository import insert_daily_report
from llm.codex_client import CodexClient
from utils.logger import get_logger

logger = get_logger("daily_evaluator")


class DailyEvaluator:
    """일일 성과를 AI로 평가하여 보고서를 생성한다."""

    def __init__(self, codex: CodexClient, tracker: PerformanceTracker):
        self.codex = codex
        self.tracker = tracker

    async def evaluate(self, market_regime: str = "") -> dict:
        """일일 평가를 수행한다."""
        summary = self.tracker.get_daily_summary()
        summary["market_regime"] = market_regime

        ai_eval = await self.codex.evaluate_daily(summary)

        insert_daily_report(
            report_date=summary["date"],
            total_pnl=summary["total_pnl"],
            realized_pnl=summary["total_pnl"],
            unrealized_pnl=0,
            trade_count=summary["total_trades"],
            win_rate=summary["win_rate"],
            market_regime=market_regime,
            ai_evaluation=ai_eval,
        )

        report = {**summary, "ai_evaluation": ai_eval}
        logger.info("일일 평가 완료: PnL=%+.0f, 승률=%.0f%%", summary["total_pnl"], summary["win_rate"])
        return report
