"""신호 생성기: Final_Score = Σ(Signal × Weight × Confidence)."""

from dataclasses import dataclass

import pandas as pd
import yaml
from pathlib import Path

from analysis.market_regime import MarketRegime
from core.strategy_engine import StrategyEngine
from data.db.repository import insert_signal
from strategies.base_strategy import Signal, SignalType
from utils.logger import get_logger

logger = get_logger("signal_generator")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class FinalSignal:
    """최종 합산 신호."""
    stock_code: str
    final_score: float          # -N ~ +N
    signal_type: SignalType     # BUY / SELL / HOLD
    component_signals: list[Signal]
    market_regime: MarketRegime


class SignalGenerator:
    """전략 신호를 가중합산하여 최종 매매 결정을 내린다."""

    def __init__(self, engine: StrategyEngine):
        self.engine = engine
        self._load_thresholds()

    def _load_thresholds(self):
        """settings.yaml에서 임계값을 로드한다."""
        with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        sig_cfg = settings.get("signal", {})
        self.buy_threshold = sig_cfg.get("buy_threshold", 0.6)
        self.sell_threshold = sig_cfg.get("sell_threshold", -0.6)

    def generate(self, stock_code: str, df: pd.DataFrame,
                 regime: MarketRegime) -> FinalSignal:
        """모든 활성 전략의 신호를 합산하여 최종 신호를 생성한다.

        Final_Score = Σ(Signal_score × Weight × Confidence)
        """
        active_strategies = self.engine.get_active_strategies(regime)
        signals: list[Signal] = []
        final_score = 0.0

        for strategy in active_strategies:
            try:
                signal = strategy.generate_signal(stock_code, df)
                weight = self.engine.get_weight(strategy.name, regime)

                weighted = signal.score * weight * signal.confidence
                final_score += weighted
                signals.append(signal)

                # DB 저장
                insert_signal(
                    stock_code=stock_code,
                    strategy=strategy.name,
                    signal_type=signal.signal_type.value,
                    score=signal.score,
                    confidence=signal.confidence,
                    final_score=weighted,
                    market_regime=regime.value,
                )

                logger.info(
                    "%s [%s] score=%.2f, weight=%.2f, conf=%.2f → %.3f",
                    stock_code, strategy.name, signal.score, weight, signal.confidence, weighted,
                )
            except Exception as e:
                logger.error("전략 %s 신호 생성 오류 (%s): %s", strategy.name, stock_code, e)

        # 최종 결정
        if final_score >= self.buy_threshold:
            signal_type = SignalType.BUY
        elif final_score <= self.sell_threshold:
            signal_type = SignalType.SELL
        else:
            signal_type = SignalType.HOLD

        result = FinalSignal(
            stock_code=stock_code,
            final_score=final_score,
            signal_type=signal_type,
            component_signals=signals,
            market_regime=regime,
        )

        logger.info(
            "%s 최종 신호: %s (score=%.3f, 전략=%d개, 장세=%s)",
            stock_code, signal_type.value, final_score, len(signals), regime.value,
        )
        return result
