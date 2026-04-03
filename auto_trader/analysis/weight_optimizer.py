"""LLM 기반 전략 가중치 자동 조정.

매일 장 마감 후 (20:00) 실행하여:
1. 오늘 각 전략이 올바른 방향을 가리켰는지 평가
2. 과거 조정 히스토리(적중/오류)를 함께 LLM에 전달
3. LLM이 전략의 설계 의도를 이해한 상태에서 가중치 조정 여부를 판단
4. 필요한 경우에만 strategies.yaml에 저장
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import yaml

from data.db.repository import get_connection, get_prices, insert_strategy_params
from utils.logger import get_logger

logger = get_logger("weight_optimizer")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "strategies.yaml"

# 각 전략의 설계 의도 — LLM 프롬프트에 포함
STRATEGY_DESCRIPTIONS = {
    "volatility_breakout": (
        "래리 윌리엄스의 변동성 돌파 전략. "
        "전일 변동폭(고가-저가)의 K배를 당일 시가에 더한 가격을 돌파하면 매수. "
        "강한 모멘텀/추세장에서 효과적이며, 횡보장이나 하락장에서는 허매수가 잦다."
    ),
    "moving_average": (
        "그랜빌의 이동평균선 전략. 단기(5일)/중기(20일)/장기(60일) 이동평균선의 "
        "배열과 교차로 추세 방향과 매매 시점을 판단. "
        "추세가 뚜렷한 장에서 강하고, 횡보장에서 휩쏘가 발생한다."
    ),
    "rsi_envelope": (
        "와일더의 RSI + 볼린저 밴드 결합 전략. "
        "RSI 과매수/과매도와 볼린저 밴드 이탈을 함께 확인하여 역추세 반등을 포착. "
        "횡보장/약세장의 과매도 반등에 강하며, 강한 추세장에서는 역추세 신호가 위험하다."
    ),
    "envelope": (
        "고전 엔벨로프 전략. 이동평균선에서 일정 비율(±5%) 벗어나면 "
        "평균 회귀를 기대하고 매매. 횡보장에서 가장 효과적이며, "
        "추세장에서는 평균 회귀가 실패할 수 있다."
    ),
    "news_sentiment": (
        "AI 뉴스 감성 분석 전략. 최근 뉴스의 긍정/부정 감성 점수를 산출하여 "
        "기술적 신호와 결합. 이벤트 드리븐 매매에 유용하나, "
        "노이즈나 과거 뉴스에 반응할 위험이 있다."
    ),
}


class WeightOptimizer:
    """LLM 기반으로 장세별 전략 가중치를 조정한다.

    수학적 알고리즘 대신 LLM이 각 전략의 설계 의도를 이해한 상태에서
    조정이 필요한 가중치만 선별적으로 변경한다.
    과거 조정 히스토리를 함께 제공하여 점차 최적값으로 수렴하도록 유도한다.
    """

    def __init__(self, codex_client=None):
        self.codex = codex_client
        self.strategy_names = [
            "volatility_breakout",
            "moving_average",
            "rsi_envelope",
            "envelope",
            "news_sentiment",
        ]
        self.regime_names = [
            "strong_bull", "weak_bull", "sideways",
            "weak_bear", "strong_bear",
        ]

    async def run(self, today_regime: str, stock_codes: list[str]) -> dict:
        """오늘의 사후 평가를 수행하고 LLM에게 가중치 조정을 요청한다.

        Args:
            today_regime: 오늘 적용된 장세.
            stock_codes: 오늘 매매 대상이었던 종목 코드 목록.

        Returns:
            {"old_weights": {...}, "new_weights": {...}, "rewards": {...},
             "llm_reasoning": str, "changed": bool}
        """
        # 1. 각 전략의 보상 계산
        rewards = self._calc_strategy_rewards(stock_codes)
        logger.info(
            "전략별 보상: %s",
            {s: f"{r:+.3f}" for s, r in rewards.items()},
        )

        # 2. 현재 가중치 로드
        config = self._load_config()
        old_weights = dict(config["regime_weights"].get(today_regime, {}))

        # 3. 과거 조정 히스토리 로드
        history = self._get_adjustment_history(today_regime, limit=14)

        # 4. LLM에게 판단 요청
        llm_result = await self._ask_llm(
            today_regime, old_weights, rewards, history
        )

        new_weights = llm_result.get("new_weights", old_weights)
        reasoning = llm_result.get("reasoning", "")
        changed = llm_result.get("changed", False)

        # 5. 변경이 있을 때만 저장
        if changed:
            config["regime_weights"][today_regime] = new_weights
            self._save_config(config)

        # 6. DB 이력 저장 (변경 여부 무관하게 기록)
        insert_strategy_params(
            strategy="regime_weights",
            params={
                "regime": today_regime,
                "old": old_weights,
                "new": new_weights,
                "rewards": rewards,
                "changed": changed,
                "reasoning": reasoning,
            },
            reason=f"LLM 가중치 판단 ({today_regime}): {'조정' if changed else '유지'}",
        )

        if changed:
            logger.info(
                "가중치 조정 (%s): %s → %s | 사유: %s",
                today_regime,
                {s: f"{v:.3f}" for s, v in old_weights.items()},
                {s: f"{v:.3f}" for s, v in new_weights.items()},
                reasoning,
            )
        else:
            logger.info(
                "가중치 유지 (%s): %s | 사유: %s",
                today_regime,
                {s: f"{v:.3f}" for s, v in old_weights.items()},
                reasoning,
            )

        return {
            "old_weights": old_weights,
            "new_weights": new_weights,
            "rewards": rewards,
            "llm_reasoning": reasoning,
            "changed": changed,
        }

    def _calc_strategy_rewards(self, stock_codes: list[str]) -> dict[str, float]:
        """오늘 각 전략이 올바른 방향을 가리켰는지 평가한다.

        Returns:
            {"volatility_breakout": 0.5, "moving_average": -0.3, ...}
        """
        today_str = date.today().isoformat()

        # 오늘 각 종목의 실제 수익률
        actual_returns = {}
        for code in stock_codes:
            rows = get_prices(code, candle_type="daily", limit=2)
            if len(rows) < 2:
                continue
            today_close = float(rows[0]["close"])
            prev_close = float(rows[1]["close"])
            if prev_close > 0:
                actual_returns[code] = (today_close - prev_close) / prev_close

        if not actual_returns:
            logger.warning("실제 수익률 계산 불가 — 보상 0 반환")
            return {s: 0.0 for s in self.strategy_names}

        # 오늘 각 전략이 낸 신호 점수 (DB signals 테이블)
        strategy_rewards = {s: [] for s in self.strategy_names}

        with get_connection() as conn:
            for code in actual_returns:
                ret = actual_returns[code]
                rows = conn.execute(
                    """SELECT strategy, score FROM signals
                       WHERE stock_code = ? AND DATE(created_at) = ?
                       ORDER BY created_at DESC""",
                    (code, today_str),
                ).fetchall()

                seen = set()
                for row in rows:
                    strategy = row["strategy"]
                    if strategy in seen or strategy not in strategy_rewards:
                        continue
                    seen.add(strategy)

                    score = float(row["score"])
                    if score == 0:
                        reward = 0.0
                    else:
                        direction_match = 1.0 if (score > 0) == (ret > 0) else -1.0
                        magnitude = min(abs(score), 1.0)
                        reward = direction_match * magnitude

                    strategy_rewards[strategy].append(reward)

        result = {}
        for s in self.strategy_names:
            if strategy_rewards[s]:
                result[s] = round(
                    sum(strategy_rewards[s]) / len(strategy_rewards[s]), 4
                )
            else:
                result[s] = 0.0

        return result

    def _get_adjustment_history(self, regime: str,
                                limit: int = 14) -> list[dict]:
        """과거 가중치 조정 히스토리를 가져온다.

        Returns:
            [{"date": "2026-03-30", "old": {...}, "new": {...},
              "rewards": {...}, "changed": bool, "reasoning": str}, ...]
        """
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT params, applied_at FROM strategy_params
                   WHERE strategy = 'regime_weights'
                   ORDER BY applied_at DESC
                   LIMIT ?""",
                (limit * 5,),  # 5개 장세가 섞여 있으므로 넉넉히
            ).fetchall()

        history = []
        for row in rows:
            try:
                params = json.loads(row["params"])
                if params.get("regime") != regime:
                    continue
                history.append({
                    "date": row["applied_at"][:10],
                    "old": params.get("old", {}),
                    "new": params.get("new", {}),
                    "rewards": params.get("rewards", {}),
                    "changed": params.get("changed", True),
                    "reasoning": params.get("reasoning", ""),
                })
                if len(history) >= limit:
                    break
            except (json.JSONDecodeError, KeyError):
                continue

        return list(reversed(history))  # 오래된 순

    async def _ask_llm(self, regime: str, current_weights: dict,
                       rewards: dict, history: list[dict]) -> dict:
        """LLM에게 가중치 조정 여부를 판단하게 한다.

        Returns:
            {"new_weights": {...}, "reasoning": str, "changed": bool}
        """
        if not self.codex:
            logger.warning("CodexClient 미설정 — 가중치 유지")
            return {
                "new_weights": current_weights,
                "reasoning": "LLM 클라이언트 없음",
                "changed": False,
            }

        # 히스토리 요약
        history_text = ""
        if history:
            lines = []
            for h in history:
                if h["changed"]:
                    changes = []
                    for s in self.strategy_names:
                        old_v = h["old"].get(s, 0)
                        new_v = h["new"].get(s, 0)
                        if old_v != new_v:
                            changes.append(f"{s}: {old_v:.3f}→{new_v:.3f}")
                    change_str = ", ".join(changes) if changes else "미세 조정"
                    lines.append(
                        f"  {h['date']}: 조정함 [{change_str}] "
                        f"보상={_fmt_rewards(h['rewards'])} "
                        f"사유=\"{h['reasoning'][:80]}\""
                    )
                else:
                    lines.append(
                        f"  {h['date']}: 유지 "
                        f"보상={_fmt_rewards(h['rewards'])} "
                        f"사유=\"{h['reasoning'][:80]}\""
                    )
            history_text = "\n".join(lines)
        else:
            history_text = "  (첫 실행 — 이전 기록 없음)"

        # 전략 설명
        strategy_desc = "\n".join(
            f"  - {name}: {STRATEGY_DESCRIPTIONS[name]}"
            for name in self.strategy_names
        )

        prompt = f"""당신은 한국 주식 자동매매 시스템의 전략 가중치를 관리하는 전문가입니다.

## 각 전략의 설계 의도
{strategy_desc}

## 현재 장세: {regime}
{_regime_description(regime)}

## 현재 가중치 (합계 = 1.0)
{json.dumps(current_weights, indent=2)}

## 오늘 각 전략의 성과 (보상)
양수 = 신호 방향이 실제 가격 변동과 일치, 음수 = 불일치
{json.dumps(rewards, indent=2)}

## 과거 {len(history)}일간 조정 히스토리 (이 장세)
{history_text}

## 판단 기준
1. **전략의 설계 의도에 맞는 장세인가?** 해당 장세에서 원래 약한 전략이 틀린 건 당연하므로 패널티를 주지 마세요.
2. **일시적 노이즈인가, 지속적 패턴인가?** 하루 결과만으로 큰 조정은 위험합니다. 히스토리에서 패턴을 확인하세요.
3. **수렴 목표**: 가중치는 점차 이 장세에 최적인 값으로 수렴해야 합니다. 한 번에 큰 변경보다 작은 조정을 반복하세요.
4. **조정 불필요 시 "유지"**: 충분한 근거가 없으면 현재 가중치를 유지하세요.
5. **제약**: 각 가중치는 0.05 이상, 합계는 정확히 1.0이어야 합니다.

## 응답 형식 (JSON만 출력)
```json
{{
  "changed": true/false,
  "reasoning": "조정/유지 사유 (한국어, 2-3문장)",
  "new_weights": {{
    "volatility_breakout": 0.XX,
    "moving_average": 0.XX,
    "rsi_envelope": 0.XX,
    "envelope": 0.XX,
    "news_sentiment": 0.XX
  }}
}}
```
changed가 false이면 new_weights는 현재 가중치와 동일하게 출력하세요."""

        try:
            response = self.codex._call(prompt, max_tokens=500)
            return self._parse_llm_response(response, current_weights)
        except Exception as e:
            logger.error("LLM 가중치 판단 실패: %s", e)
            return {
                "new_weights": current_weights,
                "reasoning": f"LLM 호출 실패: {e}",
                "changed": False,
            }

    def _parse_llm_response(self, response: str,
                            fallback_weights: dict) -> dict:
        """LLM 응답을 파싱하고 유효성을 검증한다."""
        # 코드 펜스 제거
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("LLM 응답 JSON 파싱 실패 — 가중치 유지")
            return {
                "new_weights": fallback_weights,
                "reasoning": "JSON 파싱 실패",
                "changed": False,
            }

        changed = result.get("changed", False)
        reasoning = result.get("reasoning", "")
        new_weights = result.get("new_weights", fallback_weights)

        # 유효성 검증
        if not isinstance(new_weights, dict):
            return {
                "new_weights": fallback_weights,
                "reasoning": reasoning + " (가중치 형식 오류로 유지)",
                "changed": False,
            }

        # 모든 전략이 포함되어 있는지 확인
        for s in self.strategy_names:
            if s not in new_weights:
                new_weights[s] = fallback_weights.get(s, 0.2)

        # 최소값 보장
        for s in self.strategy_names:
            new_weights[s] = max(float(new_weights[s]), 0.05)

        # 정규화 (합 = 1.0)
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {s: round(v / total, 4) for s, v in new_weights.items()}

        # 반올림 오차 보정
        diff = 1.0 - sum(new_weights.values())
        if abs(diff) > 0.0001:
            max_key = max(new_weights, key=new_weights.get)
            new_weights[max_key] = round(new_weights[max_key] + diff, 4)

        return {
            "new_weights": new_weights,
            "reasoning": reasoning,
            "changed": changed,
        }

    def _load_config(self) -> dict:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _save_config(self, config: dict):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        logger.info("strategies.yaml 가중치 저장 완료")


def _fmt_rewards(rewards: dict) -> str:
    """보상 딕셔너리를 간결한 문자열로 포맷한다."""
    if not rewards:
        return "{}"
    parts = [f"{k[:3]}:{v:+.2f}" for k, v in rewards.items()]
    return "{" + ", ".join(parts) + "}"


def _regime_description(regime: str) -> str:
    """장세에 대한 간단한 설명을 반환한다."""
    descs = {
        "strong_bull": "강한 상승장 — 모멘텀/추세 전략이 유리, 역추세 전략은 위험",
        "weak_bull": "약한 상승장 — 추세 전략이 유리하나 변동성 주의",
        "sideways": "횡보장 — 평균 회귀/역추세 전략이 유리, 추세 전략은 휩쏘 위험",
        "weak_bear": "약한 하락장 — 역추세(과매도 반등) 전략 유리, 모멘텀 전략 약화",
        "strong_bear": "강한 하락장 — 매수 자체가 위험, 역추세 반등만 선별적 매매",
    }
    return descs.get(regime, regime)
