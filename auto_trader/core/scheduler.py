"""스케줄 관리자: APScheduler 기반 일일 타임테이블."""

import json
from datetime import date, datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from utils.logger import get_logger

logger = get_logger("scheduler")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# KRX 공휴일 (수동 관리 또는 API 업데이트)
HOLIDAYS_FILE = CONFIG_DIR / "holidays.json"


class TradingScheduler:
    """매매 스케줄 관리자."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self.holidays: set[str] = set()
        self._load_holidays()
        self._load_schedule()

    def _load_holidays(self):
        """휴장일 목록을 로드한다."""
        if HOLIDAYS_FILE.exists():
            data = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
            self.holidays = set(data.get("holidays", []))
            logger.info("휴장일 %d일 로드됨", len(self.holidays))
        else:
            # 기본 파일 생성
            HOLIDAYS_FILE.write_text(
                json.dumps({"holidays": [], "updated": date.today().isoformat()},
                           indent=2),
                encoding="utf-8",
            )

    def _load_schedule(self):
        """settings.yaml에서 스케줄을 로드한다."""
        with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
            self.settings = yaml.safe_load(f)
        self.schedule_cfg = self.settings.get("schedule", {})

    def is_trading_day(self, d: date | None = None) -> bool:
        """오늘이 거래일인지 확인한다."""
        d = d or date.today()
        # 주말 체크
        if d.weekday() >= 5:
            return False
        # 공휴일 체크
        if d.isoformat() in self.holidays:
            return False
        return True

    def register_job(self, job_id: str, func, hour: int, minute: int,
                     day_of_week: str = "mon-fri", **kwargs):
        """크론 작업을 등록한다."""
        self.scheduler.add_job(
            func,
            CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week),
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.debug("작업 등록: %s (%02d:%02d)", job_id, hour, minute)

    def register_interval_job(self, job_id: str, func, seconds: int, **kwargs):
        """인터벌 작업을 등록한다."""
        self.scheduler.add_job(
            func,
            "interval",
            seconds=seconds,
            id=job_id,
            replace_existing=True,
            **kwargs,
        )

    def start(self):
        """스케줄러를 시작한다."""
        self.scheduler.start()
        logger.info("스케줄러 시작")

    def stop(self):
        """스케줄러를 중지한다."""
        self.scheduler.shutdown()
        logger.info("스케줄러 종료")

    def get_next_run_times(self) -> list[dict]:
        """다음 실행 예정 작업 목록을 반환한다."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run": str(job.next_run_time),
            })
        return jobs
