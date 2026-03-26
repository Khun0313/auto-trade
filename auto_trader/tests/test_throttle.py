"""utils/throttle.py 단위 테스트."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.throttle import SlidingWindowThrottle


@pytest.mark.asyncio
async def test_throttle_limits_rate():
    """초당 최대 호출 수를 초과하지 않는지 확인."""
    throttler = SlidingWindowThrottle(max_calls=5, window_sec=1.0)

    timestamps = []
    for _ in range(10):
        await throttler.acquire()
        timestamps.append(time.monotonic())

    # 처음 5개는 즉시 실행, 나머지 5개는 ~1초 후
    first_batch = timestamps[4] - timestamps[0]
    assert first_batch < 0.2  # 첫 5건은 거의 즉시

    # 6번째 호출은 최소 ~0.8초 이후
    gap = timestamps[5] - timestamps[0]
    assert gap >= 0.8


@pytest.mark.asyncio
async def test_throttle_allows_within_limit():
    """제한 내 호출은 대기 없이 즉시 처리되는지 확인."""
    throttler = SlidingWindowThrottle(max_calls=10, window_sec=1.0)

    start = time.monotonic()
    for _ in range(10):
        await throttler.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.3  # 10건 제한 내이므로 거의 즉시


@pytest.mark.asyncio
async def test_stats():
    """통계 카운터가 정확한지 확인."""
    throttler = SlidingWindowThrottle(max_calls=5, window_sec=1.0)

    for _ in range(3):
        await throttler.acquire()

    stats = throttler.get_stats()
    assert stats["total_calls"] == 3
    assert stats["max_calls_per_sec"] == 5
