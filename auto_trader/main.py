"""메인 진입점: 기동 → 토큰 → 스케줄 → 이벤트 루프."""

import asyncio
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

import yaml

from analysis.market_regime import MarketRegimeClassifier
from analysis.performance_tracker import PerformanceTracker
from analysis.strategy_evaluator import StrategyEvaluator
from core.auth import KISAuth
from core.data_collector import DataCollector
from core.order_executor import OrderExecutor
from core.risk_manager import RiskManager
from core.scheduler import TradingScheduler
from core.signal_generator import SignalGenerator
from core.strategy_engine import StrategyEngine
from core.news_collector import NewsCollector
from llm.codex_client import CodexClient
from llm.codex_auth import check_refresh_token_expiry_warning
from llm.daily_evaluator import DailyEvaluator
from llm.weekly_upgrader import WeeklyUpgrader
from screener.stock_screener import StockScreener
from screener.watchlist_manager import WatchlistManager
from screener.orphan_checker import OrphanChecker
from rebalancing.asset_allocator import AssetAllocator
from rebalancing.etf_watchlist import ETFWatchlist
from strategies.volatility_breakout import VolatilityBreakout
from strategies.moving_average import MovingAverage
from strategies.rsi_envelope import RSIEnvelope
from strategies.envelope import Envelope
from strategies.news_sentiment import NewsSentiment
from notifications.discord_bot import TradingBot
from utils.logger import setup_logger
from utils.throttle import init_throttler

CONFIG_DIR = Path(__file__).resolve().parent / "config"

logger = setup_logger("main", level="INFO")


class AutoTrader:
    """자동매매 시스템 메인 클래스."""

    def __init__(self):
        logger.info("=== 국내 주식 자동매매 시스템 시작 ===")

        # 설정 로드
        with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
            self.settings = yaml.safe_load(f)
        with open(CONFIG_DIR / "strategies.yaml", "r", encoding="utf-8") as f:
            self.strategy_config = yaml.safe_load(f)

        # 핵심 모듈 초기화
        self.auth = KISAuth()
        logger.info("모드: %s, API: %s", self.auth.mode, self.auth.base_url)

        # 쓰로틀러
        rps = self.settings["throttle"][self.auth.mode]["requests_per_second"]
        init_throttler(rps)

        # 모듈 초기화
        self.collector = DataCollector(self.auth)
        self.risk = RiskManager()
        self.executor = OrderExecutor(self.auth, self.risk)
        self.regime_classifier = MarketRegimeClassifier()
        self.engine = StrategyEngine()
        self.signal_gen = SignalGenerator(self.engine)
        self.screener = StockScreener()
        self.watchlist = WatchlistManager()
        self.orphan_checker = OrphanChecker()
        self.allocator = AssetAllocator()
        self.etf_watchlist = ETFWatchlist()
        self.news_collector = NewsCollector()
        self.codex = CodexClient()
        self.tracker = PerformanceTracker()
        self.daily_eval = DailyEvaluator(self.codex, self.tracker)
        self.strategy_eval = StrategyEvaluator(self.tracker)
        self.weekly_upgrader = WeeklyUpgrader(self.codex)
        self.scheduler = TradingScheduler()
        self.discord_bot = TradingBot(system_ref=self)

        # 전략 등록
        self._register_strategies()

        # 상태
        self._trading_active = True
        self._running = True

    def _register_strategies(self):
        """전략을 등록한다."""
        params = self.strategy_config.get("strategies", {})
        self.engine.register_strategy(VolatilityBreakout(params.get("volatility_breakout", {})))
        self.engine.register_strategy(MovingAverage(params.get("moving_average", {})))
        self.engine.register_strategy(RSIEnvelope(params.get("rsi_envelope", {})))
        self.engine.register_strategy(Envelope(params.get("envelope", {})))
        self.engine.register_strategy(NewsSentiment(params.get("news_sentiment", {})))

    def get_status(self) -> dict:
        """시스템 상태를 반환한다."""
        cb_active, cb_reason = self.risk.is_circuit_breaker_active()
        return {
            "mode": self.auth.mode,
            "trading_active": self._trading_active,
            "circuit_breaker": cb_active,
            "cb_reason": cb_reason,
            "regime": self.regime_classifier.current_regime.value,
            "watchlist_count": len(self.watchlist.get_active_watchlist()),
        }

    def pause_trading(self):
        self._trading_active = False
        logger.warning("매매 일시 중지")

    def resume_trading(self):
        self._trading_active = True
        logger.info("매매 재개")

    # ------------------------------------------------------------------
    # 스케줄 작업
    # ------------------------------------------------------------------

    async def _morning_news(self):
        """08:00 아침 뉴스 수집 + 감성 분석."""
        news = await self.news_collector.collect_all()
        if news:
            results = await self.codex.analyze_sentiment(news)
            for r in results:
                from data.db.repository import insert_news
                insert_news(
                    title=r.get("title", ""),
                    source="ai_analyzed",
                    sentiment_score=r.get("sentiment_score"),
                    stock_codes=r.get("stock_codes"),
                    summary=r.get("summary"),
                )

    async def _daily_report(self):
        """15:40 일일 보고서."""
        regime = self.regime_classifier.current_regime.value
        report = await self.daily_eval.evaluate(market_regime=regime)
        logger.info("일일 보고서 생성 완료")

    async def _check_oauth_expiry(self):
        """08:50 ChatGPT OAuth refresh token 만료 사전 경고."""
        await check_refresh_token_expiry_warning(bot=self.discord_bot)

    async def _cancel_pending(self):
        """15:30 미체결 취소."""
        await self.executor.cancel_pending_orders()

    # ------------------------------------------------------------------
    # 메인 루프
    # ------------------------------------------------------------------

    async def run(self):
        """메인 이벤트 루프."""
        # 휴장일 확인
        if not self.scheduler.is_trading_day():
            logger.info("오늘은 휴장일입니다.")
            return

        # 토큰 갱신
        self.auth.get_token()

        # 스케줄 등록
        self.scheduler.register_job("oauth_expiry_check", self._check_oauth_expiry, 8, 50,
                                     day_of_week="mon-sun")  # 주말 포함 매일 체크
        self.scheduler.register_job("morning_news", self._morning_news, 8, 0)
        self.scheduler.register_job("noon_news", self._morning_news, 12, 0)
        self.scheduler.register_job("afternoon_news", self._morning_news, 14, 0)
        self.scheduler.register_job("cancel_pending", self._cancel_pending, 15, 30)
        self.scheduler.register_job("daily_report", self._daily_report, 15, 40)
        self.scheduler.start()

        logger.info("스케줄 등록 완료. 이벤트 루프 시작.")

        # 시그널 핸들러
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass  # Windows

        # Discord 봇 + 메인 루프 병렬 실행
        token = os.getenv("DISCORD_BOT_TOKEN", "")
        if not token:
            logger.warning("DISCORD_BOT_TOKEN 미설정 — Discord 봇 없이 실행")

        try:
            tasks = [self._main_loop()]
            if token:
                tasks.append(self.discord_bot.start(token))
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            pass
        finally:
            self.scheduler.stop()
            await self.collector.stop_websocket()
            if not self.discord_bot.is_closed():
                await self.discord_bot.close()
            logger.info("=== 시스템 종료 ===")

    async def _main_loop(self):
        """메인 대기 루프."""
        while self._running:
            await asyncio.sleep(1)

    def _shutdown(self):
        self._running = False


def main():
    trader = AutoTrader()
    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
