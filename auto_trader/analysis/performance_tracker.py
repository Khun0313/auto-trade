"""성과 추적기: 일일 PnL, 전략별 기여도."""

import sqlite3
from datetime import datetime, date
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("performance")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "auto_trader.db"


class PerformanceTracker:
    """일일/전략별 성과를 추적한다."""

    def get_daily_summary(self, target_date: date | None = None) -> dict:
        """일일 성과 요약을 반환한다."""
        d = target_date or date.today()
        date_str = d.isoformat()

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # 거래 통계
        trades = conn.execute(
            "SELECT * FROM trades WHERE DATE(created_at) = ?", (date_str,)
        ).fetchall()

        total_pnl = sum(t["pnl"] or 0 for t in trades)
        wins = sum(1 for t in trades if (t["pnl"] or 0) > 0)
        losses = sum(1 for t in trades if (t["pnl"] or 0) < 0)
        win_rate = wins / len(trades) * 100 if trades else 0

        # 전략별 통계
        strategy_stats = {}
        for t in trades:
            s = t["strategy"] or "unknown"
            if s not in strategy_stats:
                strategy_stats[s] = {"pnl": 0, "count": 0, "wins": 0}
            strategy_stats[s]["pnl"] += t["pnl"] or 0
            strategy_stats[s]["count"] += 1
            if (t["pnl"] or 0) > 0:
                strategy_stats[s]["wins"] += 1

        conn.close()

        return {
            "date": date_str,
            "total_trades": len(trades),
            "total_pnl": total_pnl,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "strategy_stats": strategy_stats,
        }

    def get_strategy_performance(self, strategy: str, days: int = 30) -> dict:
        """특정 전략의 기간 성과를 계산한다."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        trades = conn.execute(
            """SELECT * FROM trades
               WHERE strategy = ? AND created_at >= date('now', ?)
               ORDER BY created_at""",
            (strategy, f"-{days} days"),
        ).fetchall()
        conn.close()

        if not trades:
            return {"strategy": strategy, "trades": 0}

        pnls = [t["pnl"] or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_pnl = sum(pnls)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # 최대 드로다운
        cumulative = []
        running = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            running += p
            cumulative.append(running)
            peak = max(peak, running)
            dd = (peak - running) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "strategy": strategy,
            "trades": len(trades),
            "total_pnl": total_pnl,
            "win_rate": len(wins) / len(trades) * 100,
            "profit_factor": profit_factor,
            "max_drawdown": max_dd * 100,
        }
