"""주문 실행 모듈: 주문 생성/전송, 체결 확인, 미체결 관리."""

import asyncio
import uuid
from datetime import datetime, timedelta

import aiohttp

from core.auth import KISAuth
from core.risk_manager import RiskManager, PositionSize
from data.db.repository import insert_order, insert_trade, update_order_status
from utils.logger import get_logger
from utils.throttle import throttle

logger = get_logger("order_executor")


class OrderExecutor:
    """주문 실행기."""

    def __init__(self, auth: KISAuth, risk_manager: RiskManager):
        self.auth = auth
        self.risk = risk_manager
        self.base_url = auth.base_url
        self.mode = auth.mode
        self._pending_orders: dict[str, dict] = {}
        self._recent_orders: list[dict] = []   # 중복 방지용
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 주문 생성 & 전송
    # ------------------------------------------------------------------

    async def execute_buy(self, stock_code: str, stock_name: str,
                          current_price: float, signal_score: float,
                          confidence: float, position_size: PositionSize,
                          strategy: str = "") -> str | None:
        """매수 주문을 생성하여 전송한다.

        확신도별 분기: >0.8 시장가, 0.6~0.8 지정가 -0.3%
        """
        # 서킷 브레이커 확인
        active, reason = self.risk.is_circuit_breaker_active()
        if active:
            logger.warning("서킷 브레이커 활성: %s — 주문 거부", reason)
            return None

        # 동시호가 시간 차단
        if self._is_auction_time():
            logger.info("동시호가 시간 — 주문 대기")
            return None

        # 중복 주문 확인
        if self._is_duplicate(stock_code, "BUY"):
            logger.warning("중복 매수 주문 방지: %s", stock_code)
            return None

        quantity = position_size.max_quantity
        if quantity <= 0:
            logger.info("포지션 크기 0 — 주문 불가: %s", stock_code)
            return None

        # 주문 유형 결정
        if confidence > 0.8:
            order_type = "MARKET"
            price = 0
        else:
            order_type = "LIMIT"
            price = int(current_price * 0.997)  # -0.3%

        order_id = await self._send_order(stock_code, "BUY", quantity, price, order_type)
        if order_id:
            insert_order(order_id, stock_code, stock_name, "BUY",
                         order_type, quantity, price, strategy, signal_score)
            self._record_recent(stock_code, "BUY")
            logger.info("매수 주문: %s %s %d주 @ %s (%s, 전략=%s)",
                        stock_code, stock_name, quantity,
                        f"{price:,}" if price else "시장가", order_type, strategy)
        return order_id

    async def execute_sell(self, stock_code: str, stock_name: str,
                           quantity: int, current_price: float,
                           reason: str = "", strategy: str = "") -> str | None:
        """매도 주문을 생성하여 전송한다."""
        if self._is_auction_time():
            logger.info("동시호가 시간 — 매도 대기")
            return None

        if self._is_duplicate(stock_code, "SELL"):
            logger.warning("중복 매도 주문 방지: %s", stock_code)
            return None

        order_id = await self._send_order(stock_code, "SELL", quantity, 0, "MARKET")
        if order_id:
            insert_order(order_id, stock_code, stock_name, "SELL",
                         "MARKET", quantity, None, strategy, None)
            self._record_recent(stock_code, "SELL")
            logger.info("매도 주문: %s %s %d주 시장가 (사유: %s)", stock_code, stock_name, quantity, reason)
        return order_id

    @throttle
    async def _send_order(self, stock_code: str, side: str,
                          quantity: int, price: int, order_type: str) -> str | None:
        """KIS API로 주문을 전송한다."""
        if side == "BUY":
            tr_id = "VTTC0802U" if self.mode == "paper" else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.mode == "paper" else "TTTC0801U"

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        headers = self.auth.get_headers(tr_id)

        # 주문 유형 코드
        ord_dvsn = "01" if order_type == "MARKET" else "00"  # 시장가/지정가

        body = {
            "CANO": self.auth.account_no[:8],
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price) if order_type == "LIMIT" else "0",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    data = await resp.json()

            if data.get("rt_cd") == "0":
                order_id = data["output"]["ODNO"]
                self._pending_orders[order_id] = {
                    "stock_code": stock_code,
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "created_at": datetime.now(),
                }
                return order_id
            else:
                logger.error("주문 실패: %s — %s", data.get("msg_cd"), data.get("msg1"))
                self.risk.record_api_error()
                return None
        except Exception as e:
            logger.error("주문 전송 오류: %s", e)
            self.risk.record_api_error()
            return None

    # ------------------------------------------------------------------
    # 체결 확인 & 미체결 관리
    # ------------------------------------------------------------------

    def on_execution(self, order_id: str, executed_qty: int, executed_price: float,
                     fee: float = 0, tax: float = 0):
        """체결 콜백: WebSocket 체결 수신 시 호출."""
        pending = self._pending_orders.pop(order_id, None)
        if not pending:
            logger.warning("알 수 없는 주문 체결: %s", order_id)
            return

        # 슬리피지 계산
        signal_price = pending.get("price", executed_price)
        slippage = abs(executed_price - signal_price) / signal_price if signal_price > 0 else 0

        insert_trade(
            order_id=order_id,
            stock_code=pending["stock_code"],
            side=pending["side"],
            executed_qty=executed_qty,
            executed_price=executed_price,
            fee=fee,
            tax=tax,
            slippage=slippage,
        )
        update_order_status(order_id, "filled")

        # 슬리피지 경고
        if slippage > 0.005:
            logger.warning("슬리피지 경고: %s %.2f%%", order_id, slippage * 100)

        logger.info("체결: %s %s %d주 @ %s (수수료: %s, 세금: %s)",
                     pending["stock_code"], pending["side"], executed_qty,
                     f"{executed_price:,.0f}", f"{fee:,.0f}", f"{tax:,.0f}")

    async def cancel_pending_orders(self):
        """미체결 주문을 전부 취소한다 (15:30 장 마감 시)."""
        for order_id, info in list(self._pending_orders.items()):
            await self._cancel_order(order_id)
            update_order_status(order_id, "cancelled")
            logger.info("미체결 취소: %s (%s)", order_id, info["stock_code"])

        self._pending_orders.clear()

    @throttle
    async def _cancel_order(self, order_id: str):
        """주문을 취소한다."""
        tr_id = "VTTC0803U" if self.mode == "paper" else "TTTC0803U"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        headers = self.auth.get_headers(tr_id)
        body = {
            "CANO": self.auth.account_no[:8],
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    data = await resp.json()
            if data.get("rt_cd") != "0":
                logger.error("취소 실패: %s — %s", order_id, data.get("msg1"))
        except Exception as e:
            logger.error("취소 전송 오류: %s", e)

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------

    def _is_auction_time(self) -> bool:
        """동시호가 시간인지 확인한다."""
        now = datetime.now()
        t = now.hour * 100 + now.minute
        # 08:30~09:05 또는 15:20~15:30
        return (830 <= t <= 905) or (1520 <= t <= 1530)

    def _is_duplicate(self, stock_code: str, side: str) -> bool:
        """5분 내 동종 주문이 있는지 확인한다."""
        cutoff = datetime.now() - timedelta(minutes=5)
        for recent in self._recent_orders:
            if (recent["stock_code"] == stock_code
                    and recent["side"] == side
                    and recent["time"] > cutoff):
                return True
        return False

    def _record_recent(self, stock_code: str, side: str):
        """최근 주문을 기록한다."""
        self._recent_orders.append({
            "stock_code": stock_code,
            "side": side,
            "time": datetime.now(),
        })
        # 오래된 기록 제거
        cutoff = datetime.now() - timedelta(minutes=10)
        self._recent_orders = [r for r in self._recent_orders if r["time"] > cutoff]
