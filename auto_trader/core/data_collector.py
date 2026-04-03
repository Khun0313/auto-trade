"""시세 데이터 수집 모듈 (REST + WebSocket 하이브리드)."""

import asyncio
import json
from datetime import datetime, timedelta

import aiohttp
import websockets

from core.auth import KISAuth
from data.db.repository import insert_price
from utils.logger import get_logger
from utils.throttle import throttle

logger = get_logger("data_collector")


class DataCollector:
    """한국투자증권 API를 통한 시세 데이터 수집기."""

    def __init__(self, auth: KISAuth):
        self.auth = auth
        self.base_url = auth.base_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._ws_subscriptions: set[str] = set()
        self._ws_running = False

    # ==================================================================
    # REST API 수집
    # ==================================================================

    @staticmethod
    async def _request_with_retry(session, url, headers, params,
                                  max_retries: int = 3, base_delay: float = 1.0) -> dict:
        """API 요청 + rate limit 에러 시 재시도."""
        for attempt in range(max_retries):
            async with session.get(url, headers=headers, params=params) as resp:
                data = await resp.json()

            rt_cd = data.get("rt_cd")
            if rt_cd == "0":  # 정상
                return data
            if rt_cd == "1" and "초당" in data.get("msg1", ""):
                delay = base_delay * (2 ** attempt)
                logger.warning("API rate limit (%s) — %.1f초 후 재시도 (%d/%d)",
                               params.get("FID_INPUT_ISCD", ""), delay, attempt + 1, max_retries)
                await asyncio.sleep(delay)
                continue
            # 기타 에러는 바로 반환
            return data

        logger.error("API 재시도 초과: %s", params.get("FID_INPUT_ISCD", ""))
        return data  # 마지막 응답 반환

    @throttle
    async def fetch_minute_candles(self, stock_code: str, period: str = "5") -> list[dict]:
        """분봉 데이터를 조회하여 DB에 저장한다.

        Args:
            stock_code: 종목 코드 (예: "005930").
            period: 분봉 주기 ("1", "5", "10", "30", "60").
        """
        tr_id = "FHKST03010200"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers = self.auth.get_headers(tr_id)
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"),
            "FID_PW_DATA_INCU_YN": "Y",
        }

        async with aiohttp.ClientSession() as session:
            data = await self._request_with_retry(session, url, headers, params)

        candles = data.get("output2", [])
        saved = 0
        for c in candles:
            try:
                dt_str = f"{c['stck_bsop_date']}T{c['stck_cntg_hour']}"
                insert_price(
                    stock_code=stock_code,
                    dt=dt_str,
                    o=float(c["stck_oprc"]),
                    h=float(c["stck_hgpr"]),
                    l=float(c["stck_lwpr"]),
                    c=float(c["stck_prpr"]),
                    volume=int(c["cntg_vol"]),
                    candle_type="minute",
                )
                saved += 1
            except (KeyError, ValueError) as e:
                logger.warning("분봉 파싱 오류 (%s): %s", stock_code, e)

        logger.debug("%s 분봉 %d건 저장", stock_code, saved)
        return candles

    @throttle
    async def fetch_current_price(self, stock_code: str) -> dict | None:
        """현재가를 조회한다."""
        tr_id = "FHKST01010100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self.auth.get_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }

        async with aiohttp.ClientSession() as session:
            data = await self._request_with_retry(session, url, headers, params)

        output = data.get("output")
        if output:
            logger.debug("%s 현재가: %s", stock_code, output.get("stck_prpr"))
        return output

    @throttle
    async def fetch_daily_candles(self, stock_code: str, start_date: str = "",
                                  end_date: str = "", period: str = "D") -> list[dict]:
        """일봉 데이터를 조회하여 DB에 저장한다.

        API 최대 50건 제한이므로 2회 호출하여 100건까지 확보한다.
        """
        tr_id = "FHKST03010100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        end = end_date or datetime.now().strftime("%Y%m%d")
        start = start_date or (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

        all_candles: list[dict] = []
        seen_dates: set[str] = set()

        cur_end = end
        async with aiohttp.ClientSession() as session:
            for _ in range(2):
                headers = self.auth.get_headers(tr_id)
                params = {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": start,
                    "FID_INPUT_DATE_2": cur_end,
                    "FID_PERIOD_DIV_CODE": period,
                    "FID_ORG_ADJ_PRC": "0",
                }
                data = await self._request_with_retry(session, url, headers, params)
                candles = data.get("output2", [])
                if not candles:
                    break

                for c in candles:
                    dt = c.get("stck_bsop_date", "")
                    if dt and dt not in seen_dates:
                        seen_dates.add(dt)
                        all_candles.append(c)

                oldest = candles[-1].get("stck_bsop_date", "")
                if oldest <= start:
                    break
                cur_end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
                await asyncio.sleep(0.3)

        for c in all_candles:
            try:
                insert_price(
                    stock_code=stock_code,
                    dt=c["stck_bsop_date"],
                    o=float(c["stck_oprc"]),
                    h=float(c["stck_hgpr"]),
                    l=float(c["stck_lwpr"]),
                    c=float(c["stck_clpr"]),
                    volume=int(c["acml_vol"]),
                    candle_type="daily",
                )
            except (KeyError, ValueError) as e:
                logger.warning("일봉 파싱 오류 (%s): %s", stock_code, e)

        logger.debug("%s 일봉 %d건 저장", stock_code, len(all_candles))
        return all_candles

    @throttle
    async def fetch_index_daily_candles(self, index_code: str = "0001",
                                         start_date: str = "",
                                         end_date: str = "") -> list[dict]:
        """업종지수(KOSPI 등) 일봉 데이터를 조회한다.

        API가 최대 50건만 반환하므로 2회 호출하여 이어붙인다.

        Args:
            index_code: 업종 코드 ("0001"=KOSPI, "1001"=KOSDAQ 등).
        """
        tr_id = "FHKUP03500100"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
        end = end_date or datetime.now().strftime("%Y%m%d")
        start = start_date or (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

        all_candles: list[dict] = []
        seen_dates: set[str] = set()

        # 최대 2회 호출 (50건 × 2 = 100건)
        cur_end = end
        for _ in range(2):
            headers = self.auth.get_headers(tr_id)
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": index_code,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": cur_end,
                "FID_PERIOD_DIV_CODE": "D",
            }

            async with aiohttp.ClientSession() as session:
                data = await self._request_with_retry(session, url, headers, params)

            candles = data.get("output2", [])
            if not candles:
                break

            for c in candles:
                dt = c.get("stck_bsop_date", "")
                if dt and dt not in seen_dates:
                    seen_dates.add(dt)
                    all_candles.append(c)

            # 다음 호출: 가장 오래된 날짜 - 1일을 끝으로
            oldest = candles[-1].get("stck_bsop_date", "")
            if oldest <= start:
                break
            cur_end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            await asyncio.sleep(0.3)  # rate limit 방지

        # 필드명 통일 (업종지수 → 개별종목 형식)
        normalized = []
        for c in all_candles:
            try:
                normalized.append({
                    "stck_bsop_date": c["stck_bsop_date"],
                    "stck_oprc": c["bstp_nmix_oprc"],
                    "stck_hgpr": c["bstp_nmix_hgpr"],
                    "stck_lwpr": c["bstp_nmix_lwpr"],
                    "stck_clpr": c["bstp_nmix_prpr"],
                    "acml_vol": c.get("acml_vol", "0"),
                })
            except KeyError as e:
                logger.warning("지수 일봉 파싱 오류 (%s): %s", index_code, e)

        logger.debug("지수 %s 일봉 %d건 조회", index_code, len(normalized))
        return normalized

    @throttle
    async def fetch_balance(self) -> dict | None:
        """계좌 잔고를 조회한다."""
        mode = self.auth.mode
        tr_id = "VTTC8434R" if mode == "paper" else "TTTC8434R"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = self.auth.get_headers(tr_id)
        params = {
            "CANO": self.auth.account_no[:8],
            "ACNT_PRDT_CD": self.auth.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                data = await resp.json()

        logger.debug("잔고 조회 완료")
        return data

    @throttle
    async def fetch_investor_trend(self, stock_code: str) -> dict | None:
        """투자자별 매매동향을 조회한다."""
        tr_id = "FHKST01010900"
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
        headers = self.auth.get_headers(tr_id)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                data = await resp.json()

        return data.get("output")

    # ==================================================================
    # WebSocket 실시간 수집
    # ==================================================================

    async def start_websocket(self, stock_codes: list[str]):
        """WebSocket 연결을 시작하고 실시간 체결가/호가를 수신한다."""
        approval_key = self.auth.get_ws_approval_key()
        ws_url = self.auth.ws_url
        self._ws_running = True
        retry_delay = 1

        while self._ws_running:
            try:
                async with websockets.connect(ws_url, ping_interval=30) as ws:
                    self._ws = ws
                    retry_delay = 1
                    logger.info("WebSocket 연결 성공")

                    # 구독 등록
                    for code in stock_codes[:41]:  # 최대 41종목
                        await self._subscribe(ws, approval_key, code)

                    # 메시지 수신 루프
                    async for message in ws:
                        await self._handle_ws_message(message)

            except (websockets.ConnectionClosed, ConnectionError) as e:
                logger.warning("WebSocket 연결 끊김: %s (재연결 %d초 후)", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # 지수 백오프

            except Exception as e:
                logger.error("WebSocket 오류: %s", e)
                if self._ws_running:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    async def stop_websocket(self):
        """WebSocket 연결을 종료한다."""
        self._ws_running = False
        if self._ws:
            await self._ws.close()
            logger.info("WebSocket 연결 종료")

    async def update_subscriptions(self, stock_codes: list[str]):
        """동적 구독 관리: 기존 구독 해제 → 새 구독."""
        if not self._ws:
            return

        approval_key = self.auth.get_ws_approval_key()

        # 해제할 종목
        to_unsub = self._ws_subscriptions - set(stock_codes[:41])
        for code in to_unsub:
            await self._unsubscribe(self._ws, approval_key, code)

        # 새로 구독할 종목
        to_sub = set(stock_codes[:41]) - self._ws_subscriptions
        for code in to_sub:
            await self._subscribe(self._ws, approval_key, code)

    async def _subscribe(self, ws, approval_key: str, stock_code: str):
        """체결가 + 호가 구독."""
        for tr_id in ["H0STCNT0", "H0STASP0"]:
            msg = json.dumps({
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": tr_id,
                        "tr_key": stock_code,
                    }
                },
            })
            await ws.send(msg)
        self._ws_subscriptions.add(stock_code)
        logger.debug("구독 등록: %s", stock_code)

    async def _unsubscribe(self, ws, approval_key: str, stock_code: str):
        """구독 해제."""
        for tr_id in ["H0STCNT0", "H0STASP0"]:
            msg = json.dumps({
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "2",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": tr_id,
                        "tr_key": stock_code,
                    }
                },
            })
            await ws.send(msg)
        self._ws_subscriptions.discard(stock_code)
        logger.debug("구독 해제: %s", stock_code)

    async def _handle_ws_message(self, message: str):
        """WebSocket 메시지를 파싱하여 처리한다."""
        if message.startswith("{"):
            # JSON 형식 (구독 응답/에러)
            data = json.loads(message)
            header = data.get("header", {})
            if header.get("tr_id") == "PINGPONG":
                return
            logger.debug("WS 응답: %s", header.get("msg1", ""))
            return

        # 파이프 구분 실시간 데이터
        parts = message.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]
        body = parts[3]

        if tr_id == "H0STCNT0":
            # 실시간 체결
            fields = body.split("^")
            if len(fields) >= 15:
                stock_code = fields[0]
                price = float(fields[2])
                volume = int(fields[12])
                logger.debug("실시간 체결: %s @ %s (vol: %d)", stock_code, price, volume)
