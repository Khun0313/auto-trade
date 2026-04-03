"""전략 엔진: 전략 로딩, 장세별 활성화, 신호 합산."""

from pathlib import Path

import yaml

from analysis.market_regime import MarketRegime
from strategies.base_strategy import BaseStrategy, Signal, SignalType
from utils.logger import get_logger

logger = get_logger("strategy_engine")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class StrategyEngine:
    """전략 엔진 — 전략을 로딩하고 장세에 따라 가중합산한다."""

    def __init__(self):
        self.strategies: dict[str, BaseStrategy] = {}
        self.regime_weights: dict[str, dict[str, float]] = {}
        self._load_config()

    def _load_config(self):
        """strategies.yaml에서 장세별 가중치를 로드한다."""
        with open(CONFIG_DIR / "strategies.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.regime_weights = config.get("regime_weights", {})

    def register_strategy(self, strategy: BaseStrategy):
        """전략을 등록한다."""
        self.strategies[strategy.name] = strategy
        logger.info("전략 등록: %s (활성: %s)", strategy.name, strategy.enabled)

    def get_active_strategies(self, regime: MarketRegime) -> list[BaseStrategy]:
        """현재 장세에서 활성화된 전략 목록을 반환한다."""
        weights = self.regime_weights.get(regime.value, {})
        active = []
        for name, strategy in self.strategies.items():
            if strategy.enabled and weights.get(name, 0) > 0:
                active.append(strategy)
        return active

    def reload_weights(self, regime_weights: dict[str, dict[str, float]]):
        """외부에서 갱신된 장세별 가중치를 반영한다."""
        self.regime_weights = regime_weights
        logger.info("가중치 리로드 완료: %d개 장세", len(regime_weights))

    def get_weight(self, strategy_name: str, regime: MarketRegime) -> float:
        """특정 전략의 장세별 가중치를 반환한다."""
        return self.regime_weights.get(regime.value, {}).get(strategy_name, 0.0)
