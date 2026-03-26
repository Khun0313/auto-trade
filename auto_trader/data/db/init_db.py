"""SQLite DB 초기화 모듈."""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DB_DIR / "schema.sql"
DB_PATH = DB_DIR / "auto_trader.db"


def init_database(db_path: Path | None = None) -> Path:
    """스키마를 적용하여 DB를 초기화한다."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    conn.close()

    print(f"DB 초기화 완료: {path}")
    return path


if __name__ == "__main__":
    init_database()
