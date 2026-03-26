"""API 호출 유량 제한 (슬라이딩 윈도우) 모듈."""

import asyncio
import functools
import time
from collections import deque

from utils.logger import get_logger

logger = get_logger("throttle")


class SlidingWindowThrottle:
    """슬라이딩 윈도우 기반 초당 호출 수 제한기.

    Args:
        max_calls: 윈도우 내 최대 호출 수.
        window_sec: 윈도우 크기 (초). 기본 1초.
    """

    def __init__(self, max_calls: int, window_sec: float = 1.0):
        self.max_calls = max_calls
        self.window_sec = window_sec
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

        # 통계
        self.total_calls = 0
        self._minute_calls = 0
        self._minute_start = time.monotonic()

    async def acquire(self):
        """호출 슬롯을 확보한다. 초과 시 대기."""
        async with self._lock:
            now = time.monotonic()

            # 윈도우 밖 타임스탬프 제거
            while self._timestamps and (now - self._timestamps[0]) >= self.window_sec:
                self._timestamps.popleft()

            # 슬롯 부족 → 대기
            if len(self._timestamps) >= self.max_calls:
                wait_time = self.window_sec - (now - self._timestamps[0])
                if wait_time > 0:
                    logger.debug("쓰로틀 대기: %.3f초", wait_time)
                    await asyncio.sleep(wait_time)
                    # 대기 후 재정리
                    now = time.monotonic()
                    while self._timestamps and (now - self._timestamps[0]) >= self.window_sec:
                        self._timestamps.popleft()

            self._timestamps.append(time.monotonic())
            self.total_calls += 1
            self._update_stats()

    def _update_stats(self):
        """분 단위 통계를 업데이트한다."""
        now = time.monotonic()
        self._minute_calls += 1

        elapsed = now - self._minute_start
        if elapsed >= 60:
            logger.info(
                "쓰로틀 통계: 최근 1분 %d건 (총 %d건, 제한: %d/초)",
                self._minute_calls,
                self.total_calls,
                self.max_calls,
            )
            self._minute_calls = 0
            self._minute_start = now

    def get_stats(self) -> dict:
        """현재 통계를 반환한다."""
        return {
            "total_calls": self.total_calls,
            "max_calls_per_sec": self.max_calls,
            "current_window_usage": len(self._timestamps),
        }


# 전역 쓰로틀러 인스턴스 (모듈 로드 시 설정에서 초기화)
_throttler: SlidingWindowThrottle | None = None


def init_throttler(max_calls: int):
    """전역 쓰로틀러를 초기화한다."""
    global _throttler
    _throttler = SlidingWindowThrottle(max_calls)
    logger.info("쓰로틀러 초기화: %d건/초", max_calls)


def get_throttler() -> SlidingWindowThrottle:
    """전역 쓰로틀러를 반환한다."""
    if _throttler is None:
        raise RuntimeError("쓰로틀러가 초기화되지 않았습니다. init_throttler()를 먼저 호출하세요.")
    return _throttler


def throttle(func):
    """API 함수에 쓰로틀링을 적용하는 데코레이터."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        await get_throttler().acquire()
        return await func(*args, **kwargs)

    return wrapper
