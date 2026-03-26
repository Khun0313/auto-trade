"""백테스팅 엔진: 과거 데이터 재생, 전략 시뮬레이션, 거래비용 반영."""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, SignalType
from utils.logger import get_logger

logger = get_logger("backtester")


# 거래 비용
COMMISSION_PCT = 0.00015   # 수수료 0.015%
TAX_PCT = 0.0018           # 세금 0.18% (매도 시)
SLIPPAGE_PCT = 0.002       # 슬리피지 0.2%


@dataclass
class BacktestTrade:
    entry_date: str
    exit_date: str
    stock_code: str
    side: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    strategy: str


@dataclass
class BacktestResult:
    strategy: str
    period: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate: float
    total_trades: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class Backtester:
    """백테스팅 엔진."""

    def __init__(self, initial_capital: float = 10_000_000):
        self.initial_capital = initial_capital

    def run(self, strategy: BaseStrategy, df: pd.DataFrame,
            stock_code: str = "BACKTEST",
            train_ratio: float = 0.8) -> dict[str, BacktestResult]:
        """백테스트를 실행한다.

        Args:
            strategy: 전략 인스턴스.
            df: OHLCV 데이터프레임 (오름차순).
            stock_code: 종목코드.
            train_ratio: Walk-Forward 학습 비율.

        Returns:
            {"in_sample": BacktestResult, "out_of_sample": BacktestResult}
        """
        split_idx = int(len(df) * train_ratio)
        is_df = df.iloc[:split_idx]
        oos_df = df.iloc[split_idx:]

        is_result = self._simulate(strategy, is_df, stock_code, "in_sample")
        oos_result = self._simulate(strategy, oos_df, stock_code, "out_of_sample")

        logger.info(
            "[%s] IS: 수익=%.1f%%, Sharpe=%.2f, MDD=%.1f%%, PF=%.2f, 승률=%.0f%%",
            strategy.name, is_result.total_return_pct, is_result.sharpe_ratio,
            is_result.max_drawdown_pct, is_result.profit_factor, is_result.win_rate,
        )
        logger.info(
            "[%s] OOS: 수익=%.1f%%, Sharpe=%.2f, MDD=%.1f%%, PF=%.2f, 승률=%.0f%%",
            strategy.name, oos_result.total_return_pct, oos_result.sharpe_ratio,
            oos_result.max_drawdown_pct, oos_result.profit_factor, oos_result.win_rate,
        )

        return {"in_sample": is_result, "out_of_sample": oos_result}

    def _simulate(self, strategy: BaseStrategy, df: pd.DataFrame,
                  stock_code: str, period_name: str) -> BacktestResult:
        """단일 기간 시뮬레이션."""
        capital = self.initial_capital
        position = 0
        entry_price = 0.0
        entry_date = ""
        trades: list[BacktestTrade] = []
        equity_curve = [capital]

        min_bars = 60  # 전략에 필요한 최소 데이터

        for i in range(min_bars, len(df)):
            window = df.iloc[:i + 1]
            bar = df.iloc[i]
            price = float(bar["close"])
            dt = str(bar.get("dt", bar.name))

            signal = strategy.generate_signal(stock_code, window)

            if position == 0 and signal.signal_type == SignalType.BUY:
                # 매수
                buy_price = price * (1 + SLIPPAGE_PCT)
                commission = buy_price * COMMISSION_PCT
                max_qty = int(capital * 0.95 / (buy_price + commission))
                if max_qty > 0:
                    cost = max_qty * (buy_price + commission)
                    capital -= cost
                    position = max_qty
                    entry_price = buy_price
                    entry_date = dt

            elif position > 0 and signal.signal_type == SignalType.SELL:
                # 매도
                sell_price = price * (1 - SLIPPAGE_PCT)
                proceeds = position * sell_price
                commission = proceeds * COMMISSION_PCT
                tax = proceeds * TAX_PCT
                net = proceeds - commission - tax

                pnl = net - (position * entry_price)
                pnl_pct = (sell_price / entry_price - 1) * 100

                capital += net
                trades.append(BacktestTrade(
                    entry_date=entry_date,
                    exit_date=dt,
                    stock_code=stock_code,
                    side="LONG",
                    entry_price=entry_price,
                    exit_price=sell_price,
                    quantity=position,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    strategy=strategy.name,
                ))
                position = 0

            # 평가금
            equity = capital + (position * price if position > 0 else 0)
            equity_curve.append(equity)

        # 미청산 포지션 정리
        if position > 0:
            final_price = float(df.iloc[-1]["close"]) * (1 - SLIPPAGE_PCT)
            proceeds = position * final_price
            commission = proceeds * COMMISSION_PCT
            tax = proceeds * TAX_PCT
            capital += proceeds - commission - tax

        final_capital = capital
        total_return = (final_capital / self.initial_capital - 1) * 100

        # 성과 지표 계산
        pnls = [t.pnl_pct for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / len(trades) * 100 if trades else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        sharpe = self._calc_sharpe(equity_curve)
        max_dd = self._calc_max_drawdown(equity_curve)

        return BacktestResult(
            strategy=strategy.name,
            period=period_name,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return_pct=total_return,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            profit_factor=profit_factor,
            win_rate=win_rate,
            total_trades=len(trades),
            trades=trades,
            equity_curve=equity_curve,
        )

    def _calc_sharpe(self, equity_curve: list[float], risk_free: float = 0.035) -> float:
        """연간화 샤프 비율을 계산한다."""
        if len(equity_curve) < 2:
            return 0.0

        returns = pd.Series(equity_curve).pct_change().dropna()
        if returns.std() == 0:
            return 0.0

        daily_rf = risk_free / 252
        excess = returns.mean() - daily_rf
        return float(excess / returns.std() * np.sqrt(252))

    def _calc_max_drawdown(self, equity_curve: list[float]) -> float:
        """최대 드로다운(%)을 계산한다."""
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd
