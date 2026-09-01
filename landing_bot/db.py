"""Легка SQLite-БД лендинг-бота — користувачі та джерело трафіку, з якого прийшли."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "landing.db"


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                acquisition_source TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def save_user_if_new(tg_id: int, username: str | None, source: str | None) -> bool:
    """Записує користувача при першому /start. Повертає True, якщо це новий користувач
    (джерело фіксується лише один раз, при першому візиті)."""
    with _connect() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO users (tg_id, username, acquisition_source, created_at) VALUES (?, ?, ?, ?)",
            (tg_id, username, source, datetime.utcnow().isoformat()),
        )
        return True


def acquisition_stats() -> list[tuple[str, int]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT COALESCE(acquisition_source, '(без джерела)') AS source, COUNT(*) AS cnt "
            "FROM users GROUP BY acquisition_source ORDER BY cnt DESC"
        ).fetchall()
    return rows
