import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "./data/health.db")


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    _ensure_dir()
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                weight_kg REAL,
                calories_kcal INTEGER,
                steps INTEGER,
                sleep_hours REAL,
                resting_hr_bpm INTEGER,
                workout_type TEXT,
                workout_duration_min INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON metrics(date)")
        conn.commit()


def insert_metric(data: dict):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO metrics (
                timestamp, date, weight_kg, calories_kcal, steps,
                sleep_hours, resting_hr_bpm, workout_type, workout_duration_min
            ) VALUES (
                :timestamp, :date, :weight_kg, :calories_kcal, :steps,
                :sleep_hours, :resting_hr_bpm, :workout_type, :workout_duration_min
            )
            """,
            data,
        )
        conn.commit()


def get_all_metrics():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(row) for row in rows]
