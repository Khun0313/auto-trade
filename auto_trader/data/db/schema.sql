-- 주가 데이터 (분봉/일봉)
CREATE TABLE IF NOT EXISTS stock_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    dt TEXT NOT NULL,                  -- ISO 8601
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    candle_type TEXT NOT NULL DEFAULT 'minute',  -- minute / daily
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(stock_code, dt, candle_type)
);

-- 주문
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,     -- KIS 주문번호
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    side TEXT NOT NULL,                -- BUY / SELL
    order_type TEXT NOT NULL,          -- MARKET / LIMIT
    quantity INTEGER NOT NULL,
    price REAL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / filled / partial / cancelled
    strategy TEXT,
    signal_score REAL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 체결 (거래 기록)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    side TEXT NOT NULL,
    executed_qty INTEGER NOT NULL,
    executed_price REAL NOT NULL,
    fee REAL DEFAULT 0,
    tax REAL DEFAULT 0,
    slippage REAL DEFAULT 0,
    pnl REAL,
    strategy TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

-- 전략 신호
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signal_type TEXT NOT NULL,         -- BUY / SELL / HOLD
    score REAL NOT NULL,
    confidence REAL NOT NULL,
    final_score REAL,
    market_regime TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 뉴스
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    title_hash TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    stock_codes TEXT,                  -- JSON array
    sentiment_score REAL,
    summary TEXT,
    collected_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 일일 보고서
CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT UNIQUE NOT NULL,
    total_pnl REAL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    trade_count INTEGER,
    win_rate REAL,
    market_regime TEXT,
    ai_evaluation TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 주간 전략 평가
CREATE TABLE IF NOT EXISTS weekly_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    strategy TEXT NOT NULL,
    sharpe_ratio REAL,
    max_drawdown REAL,
    profit_factor REAL,
    win_rate REAL,
    total_trades INTEGER,
    ai_suggestion TEXT,
    param_changes TEXT,               -- JSON: 변경된 파라미터
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(week_start, strategy)
);

-- 전략 파라미터 이력
CREATE TABLE IF NOT EXISTS strategy_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    params TEXT NOT NULL,              -- JSON
    reason TEXT,
    applied_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_prices_code_dt ON stock_prices(stock_code, dt);
CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(stock_code);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(stock_code);
CREATE INDEX IF NOT EXISTS idx_signals_code_dt ON signals(stock_code, created_at);
CREATE INDEX IF NOT EXISTS idx_news_hash ON news(title_hash);
