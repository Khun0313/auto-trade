"""리스크 관리 모듈: 포지션 사이징, 손절/익절, 서킷브레이커."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from utils.logger import get_logger

logger = get_logger("risk_manager")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class PositionSize:
    """포지션 크기 계산 결과."""
    stock_code: str
    max_amount: float     # 최대 투자 금액
    max_quantity: int      # 최대 수량
    risk_per_trade: float  # 거래당 위험 금액


@dataclass
class StopResult:
    """손절/익절 판단 결과."""
    stock_code: str
    action: str            # "stop_loss", "trailing_stop", "partial_take_profit", "full_take_profit", "time_stop", "hold"
    reason: str
    sell_ratio: float      # 매도 비율 (0.0 ~ 1.0)


class RiskManager:
    """리스크 관리자."""

    def __init__(self):
        self._load_config()
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._weekly_pnl = 0.0
        self._api_errors = 0
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""

    def _load_config(self):
        with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)

        risk = settings["risk"]
        self.max_position_pct = risk["max_position_pct"] / 100
        self.trade_risk_pct = risk["trade_risk_pct"] / 100
        self.daily_loss_limit_pct = risk["daily_loss_limit_pct"] / 100
        self.max_positions = risk["max_positions"]
        self.min_cash_pct = risk["min_cash_pct"] / 100
        self.stop_loss_pct = risk["stop_loss_pct"] / 100
        self.trailing_stop_pct = risk["trailing_stop_pct"] / 100
        self.partial_tp_pct = risk["partial_take_profit_pct"] / 100
        self.full_tp_pct = risk["full_take_profit_pct"] / 100
        self.time_stop_days = risk["time_stop_days"]

        cb = settings["circuit_breaker"]
        self.cb_daily_loss = cb["daily_loss_pct"] / 100
        self.cb_consecutive = cb["consecutive_losses"]
        self.cb_weekly_loss = cb["weekly_loss_pct"] / 100
        self.cb_api_errors = cb["api_error_count"]

    # ------------------------------------------------------------------
    # 포지션 사이징
    # ------------------------------------------------------------------

    def calculate_position_size(self, stock_code: str, current_price: float,
                                total_equity: float, cash: float,
                                current_positions: int) -> PositionSize:
        """포지션 크기를 계산한다.

        제약: 단일종목 10%, 거래위험 2%, 최대 10종목, 현금 최소 20%.
        """
        # 포지션 수 제한
        if current_positions >= self.max_positions:
            return PositionSize(stock_code, 0, 0, 0)

        # 최소 현금 확보
        available_cash = cash - (total_equity * self.min_cash_pct)
        if available_cash <= 0:
            return PositionSize(stock_code, 0, 0, 0)

        # 단일종목 최대 비중
        max_by_position = total_equity * self.max_position_pct

        # 거래당 위험
        risk_amount = total_equity * self.trade_risk_pct
        max_by_risk = risk_amount / abs(self.stop_loss_pct) if self.stop_loss_pct != 0 else max_by_position

        max_amount = min(max_by_position, max_by_risk, available_cash)
        max_quantity = int(max_amount / current_price) if current_price > 0 else 0

        return PositionSize(
            stock_code=stock_code,
            max_amount=max_amount,
            max_quantity=max_quantity,
            risk_per_trade=risk_amount,
        )

    # ------------------------------------------------------------------
    # 손절/익절
    # ------------------------------------------------------------------

    def check_stop(self, stock_code: str, buy_price: float, current_price: float,
                   highest_since_buy: float, buy_date: datetime,
                   is_dividend_ex_date: bool = False) -> StopResult:
        """손절/익절 조건을 체크한다."""
        pnl_pct = (current_price - buy_price) / buy_price
        holding_days = (datetime.now() - buy_date).days

        # 배당락일 손절 기준 조정
        adjusted_stop_loss = self.stop_loss_pct
        if is_dividend_ex_date:
            adjusted_stop_loss = self.stop_loss_pct * 1.5  # 1.5배 여유

        # 손절매
        if pnl_pct <= adjusted_stop_loss:
            return StopResult(stock_code, "stop_loss",
                              f"손절 {pnl_pct*100:+.1f}% (기준 {adjusted_stop_loss*100:.1f}%)", 1.0)

        # 추적 손절 (고점 대비)
        if highest_since_buy > buy_price:
            drawdown_from_high = (current_price - highest_since_buy) / highest_since_buy
            if drawdown_from_high <= self.trailing_stop_pct:
                return StopResult(stock_code, "trailing_stop",
                                  f"추적손절 고점대비 {drawdown_from_high*100:+.1f}%", 1.0)

        # 전량 익절
        if pnl_pct >= self.full_tp_pct:
            return StopResult(stock_code, "full_take_profit",
                              f"전량익절 {pnl_pct*100:+.1f}%", 1.0)

        # 부분 익절 (50%)
        if pnl_pct >= self.partial_tp_pct:
            return StopResult(stock_code, "partial_take_profit",
                              f"부분익절 {pnl_pct*100:+.1f}%", 0.5)

        # 시간 손절
        if holding_days >= self.time_stop_days and pnl_pct < 0.01:
            return StopResult(stock_code, "time_stop",
                              f"시간손절 {holding_days}일 보유 (수익 {pnl_pct*100:+.1f}%)", 1.0)

        return StopResult(stock_code, "hold", "", 0.0)

    # ------------------------------------------------------------------
    # 서킷 브레이커
    # ------------------------------------------------------------------

    def is_circuit_breaker_active(self) -> tuple[bool, str]:
        """서킷 브레이커가 발동 중인지 확인한다."""
        return self._circuit_breaker_active, self._circuit_breaker_reason

    def record_trade_result(self, pnl_pct: float):
        """거래 결과를 기록하고 서킷 브레이커를 체크한다."""
        self._daily_pnl += pnl_pct
        self._weekly_pnl += pnl_pct

        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._check_circuit_breaker()

    def record_api_error(self):
        """API 오류를 기록한다."""
        self._api_errors += 1
        self._check_circuit_breaker()

    def reset_daily(self):
        """일일 카운터를 초기화한다."""
        self._daily_pnl = 0.0
        self._api_errors = 0

    def reset_weekly(self):
        """주간 카운터를 초기화한다."""
        self._weekly_pnl = 0.0

    def reset_circuit_breaker(self):
        """서킷 브레이커를 수동 해제한다."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        logger.info("서킷 브레이커 수동 해제")

    def _check_circuit_breaker(self):
        """서킷 브레이커 발동 조건을 체크한다."""
        if abs(self._daily_pnl) >= self.cb_daily_loss:
            self._activate_cb(f"일일 손실 {self._daily_pnl*100:.1f}%")
        elif self._consecutive_losses >= self.cb_consecutive:
            self._activate_cb(f"연속 {self._consecutive_losses}회 손절")
        elif abs(self._weekly_pnl) >= self.cb_weekly_loss:
            self._activate_cb(f"주간 손실 {self._weekly_pnl*100:.1f}%")
        elif self._api_errors >= self.cb_api_errors:
            self._activate_cb(f"API 오류 {self._api_errors}회")

    def _activate_cb(self, reason: str):
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        logger.critical("서킷 브레이커 발동: %s", reason)
