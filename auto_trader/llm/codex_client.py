"""ChatGPT Codex 클라이언트: 뉴스 감성 분석 + AI 질의.

인증 우선순위:
  1. ChatGPT OAuth (Codex CLI 로그인 토큰) — ChatGPT Plus/Pro 구독 범위 내 무료
  2. OpenAI API Key (.env의 OPENAI_API_KEY) — 폴백용

OAuth 로그인:
  setup.sh 실행 시 자동으로 브라우저가 열립니다.
  또는 직접: python -m llm.codex_auth --login
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from utils.logger import get_logger
from llm.codex_auth import (
    get_auth_headers,
    get_openai_api_key_from_auth,
    get_auth_mode,
    is_logged_in,
    refresh_access_token,
    run_login,
    CODEX_RESPONSES_URL,
    OPENAI_RESPONSES_URL,
)

logger = get_logger("codex_client")
load_dotenv()

# ──────────────────────────────────────────────────────────────
# 모델 설정
# ──────────────────────────────────────────────────────────────

# ChatGPT OAuth 사용 시 모델 (chatgpt.com/backend-api 엔드포인트용)
OAUTH_MODEL = "gpt-5-codex-mini"

# API Key 폴백 시 모델 (api.openai.com 엔드포인트용)
FALLBACK_MODEL = "gpt-4o-mini"


# ──────────────────────────────────────────────────────────────
# CodexClient
# ──────────────────────────────────────────────────────────────

class CodexClient:
    """ChatGPT Codex 클라이언트.

    OAuth 토큰이 있으면 ChatGPT Plus 구독으로 무료 사용,
    없으면 OPENAI_API_KEY로 폴백한다.
    """

    def __init__(self):
        # ── 1) API Key를 항상 먼저 환경변수에 주입 (.env → auth.json 순) ──
        api_key = (
            os.getenv("OPENAI_API_KEY", "")
            or get_openai_api_key_from_auth()
            or ""
        )
        if api_key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = api_key

        # ── 2) auth_mode 확인: apiKey 모드면 OAuth 시도하지 않음 ──
        auth_mode = get_auth_mode()
        logger.info("auth_mode: %s", auth_mode)

        if auth_mode == "apiKey":
            self._use_oauth = False
            if api_key:
                logger.info("Codex API Key 모드 (auth.json의 OPENAI_API_KEY 사용)")
            else:
                logger.error(
                    "auth_mode=apiKey 이나 API Key가 없습니다. "
                    ".env에 OPENAI_API_KEY를 설정하세요."
                )
        elif auth_mode in ("chatgpt", "oauth") and is_logged_in():
            self._use_oauth = True
            logger.info(
                "ChatGPT OAuth 방식으로 초기화 (ChatGPT Plus) — "
                "엔드포인트: %s", CODEX_RESPONSES_URL
            )
        else:
            self._use_oauth = False
            if api_key:
                logger.warning(
                    "OAuth 토큰 없음. OPENAI_API_KEY 폴백 사용 중. "
                    "'python -m llm.codex_auth --login' 으로 로그인하세요."
                )
            else:
                logger.error(
                    "OAuth 토큰도 OPENAI_API_KEY도 없습니다. "
                    "setup.sh 를 실행하거나 .env에 OPENAI_API_KEY를 설정하세요."
                )

    # ──────────────────────────────────────────────
    # 내부 API 호출 메서드
    # ──────────────────────────────────────────────

    def _call_oauth(self, prompt: str, max_tokens: int = 2000) -> str:
        """ChatGPT OAuth 토큰으로 Codex 백엔드 API를 호출한다 (SSE 스트리밍).

        엔드포인트: https://chatgpt.com/backend-api/codex/responses
        opencode-openai-codex-auth 플러그인과 동일한 방식.
        Codex 백엔드는 stream=true가 필수이므로 SSE로 응답을 수신한다.
        """
        import requests

        headers = get_auth_headers()
        payload = {
            "model": OAUTH_MODEL,
            "instructions": "You are a helpful AI assistant for Korean stock market analysis.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "store": False,
            "stream": True,
        }

        try:
            resp = requests.post(
                CODEX_RESPONSES_URL,
                headers=headers,
                json=payload,
                timeout=120,
                stream=True,
            )
            resp.raise_for_status()
            return self._parse_sse_response(resp)

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            body = e.response.text[:300] if e.response is not None else ""
            logger.warning("OAuth HTTP %s: %s", status, body)

            if status == 401:
                logger.warning("OAuth 401 — refresh_token으로 토큰 갱신 시도...")
                new_token = refresh_access_token()
                if new_token:
                    logger.info("토큰 갱신 성공. 요청 재시도...")
                    headers = get_auth_headers()
                    resp2 = requests.post(
                        CODEX_RESPONSES_URL,
                        headers=headers,
                        json=payload,
                        timeout=120,
                        stream=True,
                    )
                    resp2.raise_for_status()
                    return self._parse_sse_response(resp2)
                else:
                    logger.warning("토큰 갱신 실패. OPENAI_API_KEY 폴백으로 전환합니다.")
                    _notify_relogin_required()
                    return self._call_openai_api(prompt, max_tokens)
            raise

    def _parse_sse_response(self, resp) -> str:
        """SSE 스트리밍 응답에서 텍스트를 조립한다."""
        text_parts = []
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]
            if data_str == "[DONE]":
                break
            try:
                event = json.loads(data_str)
                event_type = event.get("type", "")
                if event_type == "response.output_text.delta":
                    text_parts.append(event.get("delta", ""))
            except json.JSONDecodeError:
                continue
        result = "".join(text_parts)
        if not result:
            logger.warning("SSE 응답에서 텍스트를 추출하지 못했습니다.")
        return result

    def _call_openai_api(self, prompt: str, max_tokens: int = 2000) -> str:
        """OPENAI_API_KEY로 표준 OpenAI API를 호출한다 (폴백)."""
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model=FALLBACK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    def _call(self, prompt: str, max_tokens: int = 2000) -> str:
        """인증 방식에 따라 API를 호출한다.

        OAuth 우선, 실패 시 API Key 폴백.
        """
        if self._use_oauth:
            try:
                return self._call_oauth(prompt, max_tokens)
            except Exception as e:
                logger.warning("OAuth 호출 실패 (%s), API Key 폴백 시도...", e)
                return self._call_openai_api(prompt, max_tokens)
        else:
            return self._call_openai_api(prompt, max_tokens)

    # ──────────────────────────────────────────────
    # 공개 메서드
    # ──────────────────────────────────────────────

    async def analyze_sentiment(self, news_items: list[dict]) -> list[dict]:
        """뉴스 감성 분석을 수행한다.

        Args:
            news_items: [{"title", "url", "source"}]

        Returns:
            [{"title", "sentiment_score", "stock_codes", "summary"}]
        """
        if not news_items:
            return []

        titles = "\n".join(f"- {n['title']}" for n in news_items[:20])
        prompt = f"""다음 한국 주식시장 관련 뉴스의 감성을 분석해주세요.

뉴스 목록:
{titles}

각 뉴스에 대해 JSON 배열로 응답해주세요:
[{{"title": "뉴스 제목", "sentiment_score": -1.0~1.0, "stock_codes": ["종목코드"], "summary": "요약"}}]

- sentiment_score: -1.0(매우 부정) ~ +1.0(매우 긍정)
- stock_codes: 관련 종목코드 (6자리, 해당 없으면 빈 배열)
- summary: 한 줄 요약

JSON만 응답하세요."""

        try:
            content = self._call(prompt, max_tokens=2000)
            content = _strip_code_fence(content)
            results = json.loads(content)
            logger.info("감성 분석 완료: %d건", len(results))
            return results
        except Exception as e:
            logger.error("감성 분석 실패: %s", e)
            return []

    async def ask(self, question: str, context: str = "") -> str:
        """AI에게 질문한다."""
        prompt = question
        if context:
            prompt = f"{context}\n\n{question}"

        try:
            return self._call(prompt, max_tokens=1000)
        except Exception as e:
            logger.error("AI 질의 실패: %s", e)
            return f"오류: {e}"

    async def evaluate_daily(self, report_data: dict) -> str:
        """일일 성과를 AI로 평가한다."""
        prompt = f"""다음 자동매매 시스템의 일일 성과를 평가해주세요.

데이터:
{json.dumps(report_data, ensure_ascii=False, indent=2)}

다음을 포함해주세요:
1. 전체 성과 요약 (2-3줄)
2. 잘된 점 / 아쉬운 점
3. 내일 전략 제안"""

        return await self.ask(prompt)

    async def suggest_weekly_upgrade(self, eval_data: dict) -> dict:
        """주간 전략 업그레이드를 제안한다."""
        prompt = f"""다음 자동매매 전략의 주간 성과를 분석하고 파라미터 조정을 제안해주세요.

데이터:
{json.dumps(eval_data, ensure_ascii=False, indent=2)}

JSON으로 응답해주세요:
{{"analysis": "분석 내용", "suggestions": [{{"strategy": "전략명", "param": "파라미터", "current": 값, "suggested": 값, "reason": "이유"}}]}}"""

        response = await self.ask(prompt)
        try:
            return json.loads(_strip_code_fence(response))
        except json.JSONDecodeError:
            return {"analysis": response, "suggestions": []}


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _notify_relogin_required():
    """ChatGPT OAuth refresh 토큰 만료 시 Discord URGENT 알림을 보낸다.

    Discord 봇이 실행 중이 아닌 경우에는 로그로만 출력한다.
    """
    msg = (
        "🚨 **ChatGPT OAuth 재로그인 필요**\n"
        "refresh token이 만료되어 자동 갱신에 실패했습니다.\n"
        "AI 기능이 OPENAI\\_API\\_KEY 폴백으로 전환되었습니다.\n\n"
        "**재로그인 방법:**\n"
        "```\n"
        "# 서버(헤드리스)\n"
        "codex login --device-auth\n\n"
        "# 로컬\n"
        "python -m llm.codex_auth --login\n"
        "```"
    )

    logger.critical(
        "ChatGPT OAuth refresh token 만료 — 재로그인 필요!\n"
        "  서버: codex login --device-auth\n"
        "  로컬: python -m llm.codex_auth --login"
    )

    # Discord 봇이 실행 중이면 URGENT 알림 발송
    try:
        import asyncio
        from notifications.discord_bot import AlertLevel

        # 실행 중인 이벤트 루프가 있으면 태스크로 예약, 없으면 무시
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send_discord_alert(msg, AlertLevel.URGENT))
        except RuntimeError:
            pass  # 이벤트 루프 없음 — 로그만으로 충분
    except ImportError:
        pass  # discord 모듈 없음


async def _send_discord_alert(message: str, level):
    """실행 중인 Discord 봇 인스턴스에 알림을 전송한다."""
    try:
        from notifications.discord_bot import TradingBot
        # 봇 인스턴스는 싱글톤으로 가정 (main.py에서 관리)
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, TradingBot):
                await obj.send_alert(message, level)
                return
    except Exception as e:
        logger.error("Discord 알림 전송 실패: %s", e)


def _strip_code_fence(text: str) -> str:
    """```json ... ``` 코드 블록을 제거한다."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # 첫 줄(```json 또는 ```) 제거
        lines = lines[1:]
        # 마지막 ``` 제거
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
