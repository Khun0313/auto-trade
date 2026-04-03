"""메인 진입점: 기동 → 토큰 → 스케줄 → 이벤트 루프."""

import asyncio
import os
import signal
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from analysis.market_regime import MarketRegimeClassifier, MarketRegime
from analysis.performance_tracker import PerformanceTracker
from analysis.weight_optimizer import WeightOptimizer
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
from strategies.base_strategy import SignalType
from notifications.discord_bot import TradingBot
from data.db.repository import get_prices
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
        self.weight_optimizer = WeightOptimizer(codex_client=self.codex)
        self.scheduler = TradingScheduler()
        self.discord_bot = TradingBot(system_ref=self)

        # 전략 등록
        self._register_strategies()

        # 상태
        self._trading_active = True
        self._running = True
        self._current_regime: MarketRegime = MarketRegime.SIDEWAYS
        self._balance_cache: dict | None = None
        self._ws_task: asyncio.Task | None = None
        self._stock_names: dict[str, str] = {}  # code → 종목명 캐시

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
    # 스케줄 작업: 뉴스 / 보고서 / OAuth / 미체결
    # ------------------------------------------------------------------

    async def _morning_news(self):
        """뉴스 수집 + 감성 분석."""
        try:
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
                logger.info("뉴스 감성 분석 완료: %d건", len(results))
        except Exception as e:
            logger.error("뉴스 수집/분석 실패: %s", e)

    async def _daily_report(self):
        """15:40 일일 보고서."""
        try:
            regime = self._current_regime.value
            report = await self.daily_eval.evaluate(market_regime=regime)
            logger.info("일일 보고서 생성 완료")
        except Exception as e:
            logger.error("일일 보고서 실패: %s", e)

    async def _check_oauth_expiry(self):
        """08:50 ChatGPT OAuth refresh token 만료 사전 경고."""
        await check_refresh_token_expiry_warning(bot=self.discord_bot)

    async def _cancel_pending(self):
        """15:30 미체결 취소."""
        try:
            await self.executor.cancel_pending_orders()
            logger.info("미체결 주문 취소 완료")
        except Exception as e:
            logger.error("미체결 취소 실패: %s", e)

    # ------------------------------------------------------------------
    # 장 전 준비
    # ------------------------------------------------------------------

    async def _screening(self):
        """08:30 종목 스크리닝 — 1차(기본) + 2차(기술적) 필터."""
        try:
            logger.info("=== 08:30 종목 스크리닝 시작 ===")

            # 잔고 조회로 보유 종목 파악
            balance = await self.collector.fetch_balance()
            self._balance_cache = balance
            held_codes = []
            if balance and balance.get("output1"):
                for h in balance["output1"]:
                    if int(h.get("hldg_qty", "0")) > 0:
                        code = h["pdno"]
                        held_codes.append(code)
                        # 종목명 캐시 (잔고 API에는 prdt_name 있음)
                        if h.get("prdt_name"):
                            self._stock_names[code] = h["prdt_name"]
            self.watchlist.update_held_stocks(held_codes)

            # 감시 목록에서 후보 가져오기
            candidates = self.watchlist.get_active_watchlist()
            if not candidates:
                logger.warning("스크리닝 후보가 없습니다.")
                return

            # 1차 필터: 현재가 + 일봉(20일 평균 거래량) 기반
            phase1_input = []
            end_dt = datetime.now().strftime("%Y%m%d")
            start_dt = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")

            for code in candidates:
                price_data = await self.collector.fetch_current_price(code)
                if not price_data:
                    continue

                # hts_avls 단위: 억 원 → 원으로 변환
                market_cap_won = float(price_data.get("hts_avls", "0")) * 100_000_000

                # 20일 평균 거래량: 일봉에서 계산 (장 전에는 당일 거래량이 0)
                avg_vol_20 = 0
                daily = await self.collector.fetch_daily_candles(
                    code, start_date=start_dt, end_date=end_dt)
                if daily:
                    # 당일(거래량 미확정) 제외, 최근 20거래일
                    past_vols = [
                        int(c.get("acml_vol", 0)) for c in daily
                        if c.get("stck_bsop_date") != end_dt
                    ][:20]
                    if past_vols:
                        avg_vol_20 = sum(past_vols) // len(past_vols)

                phase1_input.append({
                    "code": code,
                    "name": self._stock_names.get(code, code),
                    "market_cap": market_cap_won,
                    "volume": int(price_data.get("acml_vol", "0")),
                    "avg_volume_20": avg_vol_20,
                })

            passed1 = self.screener.screen_phase1(phase1_input)
            logger.info("1차 스크리닝 통과: %d / %d", len(passed1), len(phase1_input))

            # 2차 필터: 일봉 기반 기술적 분석
            prices_map = {}
            for stock in passed1:
                code = stock["code"]
                await self.collector.fetch_daily_candles(code)  # DB에 일봉 저장
                df = self._get_db_daily_candles_df(code)
                if not df.empty:
                    prices_map[code] = df

            passed2 = self.screener.screen_phase2(passed1, prices_map)
            buy_candidates = [s["code"] for s in passed2]
            self.watchlist.update_buy_candidates(buy_candidates)

            logger.info("2차 스크리닝 통과 (매수 후보): %d종목 — %s",
                        len(buy_candidates), buy_candidates[:10])

        except Exception as e:
            logger.error("스크리닝 실패: %s", e)

    async def _market_analysis(self):
        """08:50 장세 판단 + WebSocket 연결."""
        try:
            logger.info("=== 08:50 장세 판단 시작 ===")

            # KOSPI 업종지수 일봉으로 장세 판단
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            kospi_candles = await self.collector.fetch_index_daily_candles(
                "0001", start_date=start, end_date=today
            )
            if kospi_candles:
                kospi_df = self._candles_to_df(kospi_candles, daily=True)
                if not kospi_df.empty:
                    self._current_regime = self.regime_classifier.classify(kospi_df)

            logger.info("현재 장세: %s", self._current_regime.value)

            # 전 종목 일봉 수집 (전략이 일봉 기반이므로 하루 1회)
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
            all_codes = self.watchlist.get_active_watchlist()
            for code in all_codes:
                try:
                    await self.collector.fetch_daily_candles(
                        code, start_date=start, end_date=today
                    )
                except Exception as e:
                    logger.warning("일봉 수집 실패 %s: %s", code, e)
            logger.info("전 종목 일봉 수집 완료: %d종목", len(all_codes))

            # WebSocket 연결
            ws_codes = self.watchlist.get_active_watchlist()
            if ws_codes:
                if self._ws_task and not self._ws_task.done():
                    await self.collector.stop_websocket()
                self._ws_task = asyncio.create_task(
                    self.collector.start_websocket(ws_codes)
                )
                logger.info("WebSocket 연결: %d종목", len(ws_codes))

        except Exception as e:
            logger.error("장세 판단 실패: %s", e)

    # ------------------------------------------------------------------
    # 핵심 트레이딩 루프 (09:05~15:20, 매 5분)
    # ------------------------------------------------------------------

    async def _trading_cycle(self):
        """매 5분마다 실행: 일봉+현재가 → 신호 생성 → 주문 실행."""
        now = datetime.now()
        # 장 시간 확인 (09:05 ~ 15:20)
        if now.hour < 9 or (now.hour == 9 and now.minute < 5):
            return
        if now.hour > 15 or (now.hour == 15 and now.minute > 20):
            return

        if not self._trading_active:
            logger.debug("매매 일시 중지 상태")
            return

        # 서킷 브레이커 확인
        cb_active, cb_reason = self.risk.is_circuit_breaker_active()
        if cb_active:
            logger.warning("서킷 브레이커 발동: %s", cb_reason)
            return

        try:
            logger.info("--- 트레이딩 사이클 시작 (%s) ---", now.strftime("%H:%M"))

            # 잔고 조회
            balance = await self.collector.fetch_balance()
            if not balance:
                logger.warning("잔고 조회 실패 — 사이클 스킵")
                return
            self._balance_cache = balance

            output2 = balance.get("output2", [{}])
            summary = output2[0] if output2 else {}
            total_equity = float(summary.get("tot_evlu_amt", "0"))
            cash = float(summary.get("dnca_tot_amt", "0"))
            holdings = balance.get("output1", [])
            current_positions = sum(
                1 for h in holdings if int(h.get("hldg_qty", "0")) > 0
            )

            # ── 매도 판단: 보유 종목 ──
            for holding in holdings:
                qty = int(holding.get("hldg_qty", "0"))
                if qty <= 0:
                    continue
                code = holding["pdno"]
                name = holding.get("prdt_name", code)
                if name and name != code:
                    self._stock_names[code] = name
                current_price = float(holding.get("prpr", "0"))

                # 일봉 + 당일 현재가 바로 신호 생성
                daily_df = self._get_db_daily_candles_df(code)
                if daily_df.empty:
                    continue
                price_data = await self.collector.fetch_current_price(code)
                if not price_data:
                    continue
                df = self._append_today_bar(daily_df, price_data)

                signal = self.signal_gen.generate(code, df, self._current_regime)

                if signal.signal_type == SignalType.SELL:
                    logger.info("매도 신호: %s (%s) score=%.2f",
                                name, code, signal.final_score)
                    strategy_name = (
                        signal.component_signals[0].strategy_name
                        if signal.component_signals else "mixed"
                    )
                    await self.executor.execute_sell(
                        stock_code=code,
                        stock_name=name,
                        quantity=qty,
                        current_price=current_price,
                        reason=f"signal_score={signal.final_score:.2f}",
                        strategy=strategy_name,
                    )

            # ── 매수 판단: 감시 목록 후보 ──
            buy_candidates = self.watchlist.get_active_watchlist()
            held_set = {
                h["pdno"] for h in holdings if int(h.get("hldg_qty", "0")) > 0
            }

            for code in buy_candidates:
                if code in held_set:
                    continue  # 이미 보유 중

                # 일봉 + 당일 현재가 바로 신호 생성
                daily_df = self._get_db_daily_candles_df(code)
                if daily_df.empty:
                    continue
                price_data = await self.collector.fetch_current_price(code)
                if not price_data:
                    continue
                df = self._append_today_bar(daily_df, price_data)

                signal = self.signal_gen.generate(code, df, self._current_regime)

                if signal.signal_type == SignalType.BUY:
                    current_price = float(price_data.get("stck_prpr", "0"))
                    stock_name = self._stock_names.get(code, code)

                    pos_size = self.risk.calculate_position_size(
                        stock_code=code,
                        current_price=current_price,
                        total_equity=total_equity,
                        cash=cash,
                        current_positions=current_positions,
                    )

                    if pos_size.max_quantity <= 0:
                        logger.debug("%s 포지션 크기 0 — 스킵", code)
                        continue

                    strategy_name = (
                        signal.component_signals[0].strategy_name
                        if signal.component_signals else "mixed"
                    )
                    confidence = (
                        signal.component_signals[0].confidence
                        if signal.component_signals else 0.5
                    )

                    logger.info("매수 신호: %s (%s) score=%.2f qty=%d",
                                stock_name, code, signal.final_score,
                                pos_size.max_quantity)
                    result = await self.executor.execute_buy(
                        stock_code=code,
                        stock_name=stock_name,
                        current_price=current_price,
                        signal_score=signal.final_score,
                        confidence=confidence,
                        position_size=pos_size.max_quantity,
                        strategy=strategy_name,
                    )
                    if result:
                        current_positions += 1

            logger.info("--- 트레이딩 사이클 완료 ---")

        except Exception as e:
            logger.error("트레이딩 사이클 오류: %s", e)

    # ------------------------------------------------------------------
    # 주기적 작업 (현재가, 잔고, 투자자동향)
    # ------------------------------------------------------------------

    async def _poll_prices(self):
        """매 1분: 감시 종목 현재가 조회."""
        try:
            watchlist = self.watchlist.get_active_watchlist()
            for code in watchlist[:20]:  # 쓰로틀링 고려
                await self.collector.fetch_current_price(code)
        except Exception as e:
            logger.error("현재가 조회 실패: %s", e)

    async def _poll_balance(self):
        """매 10분: 잔고 조회."""
        try:
            balance = await self.collector.fetch_balance()
            if balance:
                self._balance_cache = balance
                logger.debug("잔고 갱신 완료")
        except Exception as e:
            logger.error("잔고 조회 실패: %s", e)

    async def _poll_investor_trend(self):
        """매 30분: 주요 종목 투자자 동향."""
        try:
            watchlist = self.watchlist.get_active_watchlist()
            for code in watchlist[:10]:
                await self.collector.fetch_investor_trend(code)
        except Exception as e:
            logger.error("투자자 동향 조회 실패: %s", e)

    # ------------------------------------------------------------------
    # 고아 포지션 체크
    # ------------------------------------------------------------------

    async def _orphan_check(self):
        """15:40 고아 포지션(장기 미전략 보유) 점검."""
        try:
            if not self._balance_cache:
                return
            holdings = self._balance_cache.get("output1", [])
            positions = []
            active_strategies = [
                s.name for s in self.engine.get_active_strategies(self._current_regime)
            ]
            for h in holdings:
                qty = int(h.get("hldg_qty", "0"))
                if qty <= 0:
                    continue
                code = h["pdno"]
                buy_price = float(h.get("pchs_avg_pric", "0"))
                if buy_price <= 0:
                    continue

                # DB에서 최근 매수일과 전략명 조회
                from data.db.repository import get_connection
                with get_connection() as conn:
                    row = conn.execute(
                        """SELECT created_at, strategy FROM trades
                           WHERE stock_code = ? AND side = 'BUY'
                           ORDER BY created_at DESC LIMIT 1""",
                        (code,),
                    ).fetchone()
                buy_date = row["created_at"] if row else datetime.now().isoformat()
                strategy = (row["strategy"] or "") if row else ""

                positions.append({
                    "code": code,
                    "name": h.get("prdt_name", ""),
                    "buy_date": buy_date,
                    "buy_price": buy_price,
                    "current_price": float(h.get("prpr", "0")),
                    "strategy": strategy,
                })
            orphans = self.orphan_checker.check(positions, active_strategies)
            if orphans:
                logger.warning("고아 포지션 %d건 감지: %s",
                               len(orphans),
                               [(o.stock_code, o.reason) for o in orphans])
        except Exception as e:
            logger.error("고아 체크 실패: %s", e)

    async def _weight_update(self):
        """20:00 LLM 기반 전략 가중치 조정."""
        try:
            logger.info("=== 20:00 가중치 조정 시작 ===")
            regime = self._current_regime.value
            stock_codes = self.watchlist.get_active_watchlist()

            if not stock_codes:
                logger.warning("활성 종목 없음 — 가중치 조정 건너뜀")
                return

            result = await self.weight_optimizer.run(regime, stock_codes)

            if result["changed"]:
                # 전략 엔진에 새 가중치 반영
                with open(CONFIG_DIR / "strategies.yaml", "r",
                           encoding="utf-8") as f:
                    new_config = yaml.safe_load(f)
                self.strategy_config = new_config
                self.engine.reload_weights(new_config.get("regime_weights", {}))
                logger.info(
                    "가중치 조정 완료 (%s): %s",
                    regime, result["llm_reasoning"],
                )
            else:
                logger.info(
                    "가중치 유지 (%s): %s",
                    regime, result["llm_reasoning"],
                )
        except Exception as e:
            logger.error("가중치 조정 실패: %s", e)

    # ------------------------------------------------------------------
    # 주간 작업 (금요일)
    # ------------------------------------------------------------------

    async def _weekly_evaluation(self):
        """금 16:00 주간 전략 평가."""
        try:
            logger.info("=== 주간 전략 평가 시작 ===")
            today = date.today()
            week_start = (today - timedelta(days=today.weekday())).isoformat()
            week_end = today.isoformat()

            strategy_names = [s.name for s in self.engine.strategies.values()]
            eval_results = self.strategy_eval.evaluate_all(
                strategy_names, week_start, week_end
            )
            logger.info("주간 전략 평가 완료: %d개 전략", len(eval_results))
        except Exception as e:
            logger.error("주간 전략 평가 실패: %s", e)

    async def _weekly_rebalance(self):
        """금 16:30 주간 리밸런싱 + AI 전략 업그레이드."""
        try:
            logger.info("=== 주간 리밸런싱 시작 ===")

            # 잔고 기반 자산 배분 계산
            balance = await self.collector.fetch_balance()
            if not balance:
                return

            output2 = balance.get("output2", [{}])
            summary = output2[0] if output2 else {}
            total_value = float(summary.get("tot_evlu_amt", "0"))
            stock_value = float(summary.get("scts_evlu_amt", "0"))
            cash_value = float(summary.get("dnca_tot_amt", "0"))
            etf_value = 0  # ETF는 별도 추적

            plan = self.allocator.plan_rebalance(
                regime=self._current_regime,
                total_value=total_value,
                stock_value=stock_value,
                etf_value=etf_value,
                cash_value=cash_value,
            )
            logger.info("리밸런싱 필요: %s (현재: %s → 목표: %s)",
                        plan.needs_rebalance, plan.current, plan.target)

            # AI 전략 업그레이드 제안
            today = date.today()
            week_start = (today - timedelta(days=today.weekday())).isoformat()
            strategy_names = [s.name for s in self.engine.strategies.values()]
            eval_data = self.strategy_eval.evaluate_all(
                strategy_names, week_start, today.isoformat()
            )
            current_params = {
                s.name: s.get_parameters() for s in self.engine.strategies.values()
            }
            upgrade = await self.weekly_upgrader.suggest_and_apply(
                eval_data={"strategies": eval_data},
                current_params=current_params,
            )
            logger.info("AI 전략 업그레이드: %s", upgrade.get("analysis", "")[:100])

        except Exception as e:
            logger.error("주간 리밸런싱 실패: %s", e)

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------

    @staticmethod
    def _candles_to_df(candles: list[dict], daily: bool = False) -> pd.DataFrame:
        """API 응답 분봉/일봉 리스트를 DataFrame으로 변환한다.

        분봉 종가 키: stck_prpr, 일봉 종가 키: stck_clpr.
        """
        rows = []
        for c in candles:
            try:
                if daily:
                    dt = c.get("stck_bsop_date", "")
                    close_val = c.get("stck_clpr") or c.get("stck_prpr", 0)
                else:
                    dt = f"{c.get('stck_bsop_date', '')}T{c.get('stck_cntg_hour', '')}"
                    close_val = c.get("stck_prpr", 0)
                rows.append({
                    "dt": dt,
                    "open": float(c.get("stck_oprc", 0)),
                    "high": float(c.get("stck_hgpr", 0)),
                    "low": float(c.get("stck_lwpr", 0)),
                    "close": float(close_val),
                    "volume": int(c.get("cntg_vol", 0) or c.get("acml_vol", 0)),
                })
            except (ValueError, KeyError):
                continue
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
        df = df.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
        df.set_index("dt", inplace=True)
        return df

    def _get_db_daily_candles_df(self, stock_code: str,
                                  limit: int = 100) -> pd.DataFrame:
        """DB에서 일봉을 읽어 DataFrame으로 반환한다."""
        rows = get_prices(stock_code, candle_type="daily", limit=limit)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={"dt": "dt_str"})
        df["dt"] = pd.to_datetime(df["dt_str"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
        df.set_index("dt", inplace=True)
        return df

    @staticmethod
    def _append_today_bar(daily_df: pd.DataFrame,
                          price_data: dict) -> pd.DataFrame:
        """일봉 DataFrame 끝에 당일 현재가 바를 추가한다.

        장중에는 일봉 API에 당일 데이터가 없으므로
        현재가 API 응답으로 당일 바를 직접 구성한다.
        """
        today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"))
        # 이미 당일 데이터가 있으면 교체
        if not daily_df.empty and daily_df.index[-1] >= today:
            daily_df = daily_df[daily_df.index < today]

        today_row = pd.DataFrame([{
            "open": float(price_data.get("stck_oprc", 0)),
            "high": float(price_data.get("stck_hgpr", 0)),
            "low": float(price_data.get("stck_lwpr", 0)),
            "close": float(price_data.get("stck_prpr", 0)),
            "volume": int(price_data.get("acml_vol", 0)),
        }], index=pd.DatetimeIndex([today], name="dt"))

        return pd.concat([daily_df, today_row])

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

        # ── 고정 시간 스케줄 ──
        self.scheduler.register_job(
            "oauth_expiry_check", self._check_oauth_expiry, 8, 50,
            day_of_week="mon-sun")
        self.scheduler.register_job("morning_news", self._morning_news, 8, 0)
        self.scheduler.register_job("screening", self._screening, 8, 30)
        self.scheduler.register_job("market_analysis", self._market_analysis, 8, 50)
        self.scheduler.register_job("noon_news", self._morning_news, 12, 0)
        self.scheduler.register_job("afternoon_news", self._morning_news, 14, 0)
        self.scheduler.register_job("cancel_pending", self._cancel_pending, 15, 30)
        self.scheduler.register_job("daily_report", self._daily_report, 15, 40)
        self.scheduler.register_job("orphan_check", self._orphan_check, 15, 40)
        self.scheduler.register_job("weight_update", self._weight_update, 20, 0)

        # 금요일 전용
        self.scheduler.register_job(
            "weekly_evaluation", self._weekly_evaluation, 16, 0,
            day_of_week="fri")
        self.scheduler.register_job(
            "weekly_rebalance", self._weekly_rebalance, 16, 30,
            day_of_week="fri")

        # ── 반복 주기 스케줄 ──
        self.scheduler.register_interval_job(
            "trading_cycle", self._trading_cycle, seconds=300)  # 5분
        self.scheduler.register_interval_job(
            "poll_prices", self._poll_prices, seconds=60)       # 1분
        self.scheduler.register_interval_job(
            "poll_balance", self._poll_balance, seconds=600)    # 10분
        self.scheduler.register_interval_job(
            "poll_investor", self._poll_investor_trend, seconds=1800)  # 30분

        self.scheduler.start()
        logger.info("스케줄 등록 완료 (고정 9건 + 반복 4건). 이벤트 루프 시작.")

        # 시그널 핸들러
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                pass  # Windows

        # 현재 시간이 장중이면 아직 안 지난 작업만 즉시 실행
        now = datetime.now()
        if 8 <= now.hour < 16:
            logger.info("장중 기동 감지 — 미완료 작업 실행")
            if now.hour >= 8 and now.hour < 9:
                # 08:00~08:59: 스크리닝까지만 (장세 판단은 08:50 스케줄에 맡김)
                if now.minute < 30:
                    await self._morning_news()
                await self._screening()
                if now.minute >= 50:
                    await self._market_analysis()
            elif now.hour >= 9:
                # 09시 이후: 모든 장 전 작업 실행
                await self._screening()
                await self._market_analysis()

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
