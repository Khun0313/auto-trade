"""데이터 접근 레이어 (INSERT/SELECT 공통 함수)."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger("repository")

DB_PATH = Path(__file__).resolve().parent / "auto_trader.db"


@contextmanager
def get_connection(db_path: Path | None = None):
    """SQLite 커넥션 컨텍스트 매니저."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------
# 주가 데이터
# ------------------------------------------------------------------

def insert_price(stock_code: str, dt: str, o: float, h: float, l: float, c: float,
                 volume: int, candle_type: str = "minute"):
    """주가 데이터를 삽입한다 (중복 무시)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO stock_prices
               (stock_code, dt, open, high, low, close, volume, candle_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (stock_code, dt, o, h, l, c, volume, candle_type),
        )


def get_prices(stock_code: str, candle_type: str = "minute",
               start_dt: str | None = None, end_dt: str | None = None,
               limit: int = 500) -> list[dict]:
    """주가 데이터를 조회한다."""
    query = "SELECT * FROM stock_prices WHERE stock_code = ? AND candle_type = ?"
    params: list[Any] = [stock_code, candle_type]

    if start_dt:
        query += " AND dt >= ?"
        params.append(start_dt)
    if end_dt:
        query += " AND dt <= ?"
        params.append(end_dt)

    query += " ORDER BY dt DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# 주문
# ------------------------------------------------------------------

def insert_order(order_id: str, stock_code: str, stock_name: str, side: str,
                 order_type: str, quantity: int, price: float | None = None,
                 strategy: str | None = None, signal_score: float | None = None):
    """주문을 삽입한다."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO orders
               (order_id, stock_code, stock_name, side, order_type, quantity, price, strategy, signal_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, stock_code, stock_name, side, order_type, quantity, price, strategy, signal_score),
        )


def update_order_status(order_id: str, status: str):
    """주문 상태를 업데이트한다."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (status, datetime.now().isoformat(), order_id),
        )


# ------------------------------------------------------------------
# 체결
# ------------------------------------------------------------------

def insert_trade(order_id: str, stock_code: str, side: str,
                 executed_qty: int, executed_price: float,
                 fee: float = 0, tax: float = 0, slippage: float = 0,
                 pnl: float | None = None, strategy: str | None = None):
    """체결 기록을 삽입한다."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO trades
               (order_id, stock_code, side, executed_qty, executed_price, fee, tax, slippage, pnl, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, stock_code, side, executed_qty, executed_price, fee, tax, slippage, pnl, strategy),
        )


# ------------------------------------------------------------------
# 신호
# ------------------------------------------------------------------

def insert_signal(stock_code: str, strategy: str, signal_type: str,
                  score: float, confidence: float,
                  final_score: float | None = None, market_regime: str | None = None):
    """전략 신호를 저장한다."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO signals
               (stock_code, strategy, signal_type, score, confidence, final_score, market_regime)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (stock_code, strategy, signal_type, score, confidence, final_score, market_regime),
        )


# ------------------------------------------------------------------
# 뉴스
# ------------------------------------------------------------------

def insert_news(title: str, source: str, url: str | None = None,
                stock_codes: list[str] | None = None,
                sentiment_score: float | None = None, summary: str | None = None):
    """뉴스를 삽입한다 (제목 해시 기반 중복 제거)."""
    title_hash = sha256(title.encode()).hexdigest()[:16]
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO news
               (title, title_hash, source, url, stock_codes, sentiment_score, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (title, title_hash, source, url,
             json.dumps(stock_codes) if stock_codes else None,
             sentiment_score, summary),
        )


# ------------------------------------------------------------------
# 보고서
# ------------------------------------------------------------------

def insert_daily_report(report_date: str, total_pnl: float, realized_pnl: float,
                        unrealized_pnl: float, trade_count: int, win_rate: float,
                        market_regime: str, ai_evaluation: str | None = None):
    """일일 보고서를 저장한다."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_reports
               (report_date, total_pnl, realized_pnl, unrealized_pnl,
                trade_count, win_rate, market_regime, ai_evaluation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_date, total_pnl, realized_pnl, unrealized_pnl,
             trade_count, win_rate, market_regime, ai_evaluation),
        )


def insert_weekly_evaluation(week_start: str, week_end: str, strategy: str,
                             sharpe_ratio: float, max_drawdown: float,
                             profit_factor: float, win_rate: float,
                             total_trades: int, ai_suggestion: str | None = None,
                             param_changes: dict | None = None):
    """주간 전략 평가를 저장한다."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO weekly_evaluations
               (week_start, week_end, strategy, sharpe_ratio, max_drawdown,
                profit_factor, win_rate, total_trades, ai_suggestion, param_changes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (week_start, week_end, strategy, sharpe_ratio, max_drawdown,
             profit_factor, win_rate, total_trades, ai_suggestion,
             json.dumps(param_changes) if param_changes else None),
        )


def insert_strategy_params(strategy: str, params: dict, reason: str | None = None):
    """전략 파라미터 변경 이력을 저장한다."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO strategy_params (strategy, params, reason) VALUES (?, ?, ?)",
            (strategy, json.dumps(params), reason),
        )
