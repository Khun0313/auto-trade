"""Discord Bot: 알림 발송 + 명령어 처리."""

import asyncio
import os
from enum import IntEnum

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.logger import get_logger

logger = get_logger("discord_bot")

load_dotenv()


class AlertLevel(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


LEVEL_EMOJI = {
    AlertLevel.LOW: "ℹ️",
    AlertLevel.NORMAL: "📊",
    AlertLevel.HIGH: "⚠️",
    AlertLevel.URGENT: "🚨",
}


class TradingBot(commands.Bot):
    """자동매매 Discord 봇."""

    def __init__(self, system_ref=None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.system = system_ref  # 메인 시스템 참조
        self.alert_channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        self._setup_commands()

    def _setup_commands(self):
        @self.command(name="status")
        async def status(ctx):
            """시스템 상태를 조회한다."""
            if self.system:
                info = self.system.get_status() if hasattr(self.system, "get_status") else {}
            else:
                info = {"status": "시스템 미연결"}
            await ctx.send(f"```json\n{info}\n```")

        @self.command(name="balance")
        async def balance(ctx):
            """잔고를 조회한다."""
            await ctx.send("잔고 조회 중...")

        @self.command(name="today")
        async def today(ctx):
            """오늘 거래 내역을 조회한다."""
            await ctx.send("오늘 거래 내역 조회 중...")

        @self.command(name="stop")
        async def stop(ctx):
            """매매를 중지한다."""
            if self.system and hasattr(self.system, "pause_trading"):
                self.system.pause_trading()
            await ctx.send("매매 중지됨")
            logger.warning("Discord에서 매매 중지 명령 수신")

        @self.command(name="resume")
        async def resume(ctx):
            """매매를 재개한다."""
            if self.system and hasattr(self.system, "resume_trading"):
                self.system.resume_trading()
            await ctx.send("매매 재개됨")
            logger.info("Discord에서 매매 재개 명령 수신")

        @self.command(name="report")
        async def report(ctx):
            """일일 보고서를 발송한다."""
            await ctx.send("보고서 생성 중...")

        @self.command(name="ask")
        async def ask(ctx, *, question: str = ""):
            """AI에게 질문한다."""
            if not question:
                await ctx.send("질문을 입력해주세요. 예: !ask 오늘 장세는?")
                return
            if self.system and hasattr(self.system, "codex"):
                answer = await self.system.codex.ask(question)
                await ctx.send(f"**Q:** {question}\n**A:** {answer[:1900]}")
            else:
                await ctx.send("AI 모듈이 연결되지 않았습니다.")

    async def send_alert(self, message: str, level: AlertLevel = AlertLevel.NORMAL):
        """알림 메시지를 채널에 발송한다."""
        if not self.alert_channel_id:
            return

        channel = self.get_channel(self.alert_channel_id)
        if channel:
            emoji = LEVEL_EMOJI.get(level, "")
            await channel.send(f"{emoji} {message}")

    async def send_daily_report(self, report: dict):
        """일일 보고서를 발송한다."""
        msg = (
            f"📋 **일일 보고서** ({report.get('date', '')})\n"
            f"총 손익: {report.get('total_pnl', 0):+,.0f}원\n"
            f"거래: {report.get('total_trades', 0)}건 "
            f"(승률 {report.get('win_rate', 0):.0f}%)\n"
            f"장세: {report.get('market_regime', '-')}\n\n"
            f"**AI 평가:**\n{report.get('ai_evaluation', '-')[:1500]}"
        )
        await self.send_alert(msg, AlertLevel.NORMAL)

    async def on_ready(self):
        logger.info("Discord Bot 로그인: %s", self.user)
        await self.send_alert("시스템 시작됨", AlertLevel.NORMAL)


def run_bot(system_ref=None):
    """Discord Bot을 실행한다."""
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        logger.warning("DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
        return

    bot = TradingBot(system_ref=system_ref)
    bot.run(token)
