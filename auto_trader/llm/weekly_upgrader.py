"""주간 AI 업그레이드: AI 제안 → 백테스트 → 검증 후 적용."""

from data.db.repository import insert_strategy_params
from llm.codex_client import CodexClient
from utils.logger import get_logger

logger = get_logger("weekly_upgrader")


class WeeklyUpgrader:
    """주간 전략 파라미터 업그레이드."""

    def __init__(self, codex: CodexClient):
        self.codex = codex

    async def suggest_and_apply(self, eval_data: dict,
                                current_params: dict) -> dict:
        """AI 제안을 받아 파라미터를 업데이트한다.

        Args:
            eval_data: 주간 평가 데이터.
            current_params: 현재 전략 파라미터.

        Returns:
            적용된 변경사항.
        """
        suggestion = await self.codex.suggest_weekly_upgrade({
            "evaluation": eval_data,
            "current_params": current_params,
        })

        applied = []
        for s in suggestion.get("suggestions", []):
            strategy = s.get("strategy", "")
            param = s.get("param", "")
            suggested = s.get("suggested")
            reason = s.get("reason", "")

            if strategy and param and suggested is not None:
                # 이력 저장
                insert_strategy_params(
                    strategy=strategy,
                    params={param: suggested},
                    reason=reason,
                )
                applied.append(s)
                logger.info("파라미터 변경: %s.%s → %s (%s)", strategy, param, suggested, reason)

        return {
            "analysis": suggestion.get("analysis", ""),
            "applied": applied,
        }
