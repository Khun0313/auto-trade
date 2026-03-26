"""과거 데이터 수집 스크립트: 최소 3년 KOSPI + 개별종목."""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.auth import KISAuth
from core.data_collector import DataCollector
from utils.throttle import init_throttler
from utils.logger import setup_logger

logger = setup_logger("historical", level="INFO")


async def collect_historical(stock_codes: list[str], years: int = 3):
    """과거 일봉 데이터를 수집한다."""
    auth = KISAuth()
    collector = DataCollector(auth)

    # 쓰로틀러 초기화
    rps = 4 if auth.mode == "paper" else 15
    init_throttler(rps)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * years)

    logger.info("과거 데이터 수집 시작: %d종목, %d년", len(stock_codes), years)

    for code in stock_codes:
        logger.info("수집 중: %s", code)
        # API 제한으로 100일 단위로 요청
        current_end = end_date
        while current_end > start_date:
            current_start = max(current_end - timedelta(days=100), start_date)
            try:
                await collector.fetch_daily_candles(
                    stock_code=code,
                    start_date=current_start.strftime("%Y%m%d"),
                    end_date=current_end.strftime("%Y%m%d"),
                )
            except Exception as e:
                logger.error("수집 오류 %s (%s~%s): %s", code,
                             current_start.strftime("%Y%m%d"),
                             current_end.strftime("%Y%m%d"), e)
            current_end = current_start - timedelta(days=1)

    logger.info("과거 데이터 수집 완료")


if __name__ == "__main__":
    # 기본 종목: KOSPI ETF + 주요 대형주
    default_codes = [
        "069500",  # KODEX 200
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "051910",  # LG화학
    ]
    asyncio.run(collect_historical(default_codes))
