"""일별 파일 회전 로거 유틸리티."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str,
    level: str = "INFO",
    retention_days: int = 30,
) -> logging.Logger:
    """이름 기반 로거를 생성하고, 콘솔 + 일별 파일 핸들러를 붙인다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # 콘솔 핸들러
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 일별 파일 회전 핸들러
    log_file = LOG_DIR / f"{name}.log"
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """이미 설정된 로거를 가져오거나, 기본값으로 새로 생성한다."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
