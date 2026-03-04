import sqlite3
import json
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "./data/health.db")

DEFAULT_GOALS = {
    "steps": 10000,
    "calories": 2000,
    "sleep": 7,
    "target_weight_kg": None,
}

DEFAULT_PROFILE = {
    "height_cm": 175,
    "age": 30,
    "sex": "male",
}


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
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                goals_json TEXT NOT NULL DEFAULT '{}',
                profile_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                weight_kg REAL,
                calories_kcal INTEGER,
                calories_burned_kcal INTEGER,
                steps INTEGER,
                sleep_hours REAL,
                resting_hr_bpm INTEGER,
                workout_type TEXT,
                workout_duration_min INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        
        # Migration: add calories_burned_kcal column if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()]
        if "calories_burned_kcal" not in cols:
            conn.execute("ALTER TABLE metrics ADD COLUMN calories_burned_kcal INTEGER")
        
        # Migration: add user_id column if missing (existing DBs)
        # Must happen BEFORE creating the index
        cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)").fetchall()]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE metrics ADD COLUMN user_id INTEGER REFERENCES users(id)")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON metrics(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON metrics(user_id)")

        # Workouts table for individual workout sessions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                workout_type TEXT NOT NULL,
                duration_min INTEGER NOT NULL,
                calories_burned INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workouts_user_id ON workouts(user_id)")

        conn.commit()


# ── User helpers ──────────────────────────────────────────────

def create_user(username: str, password_hash: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, goals_json, profile_json) VALUES (?, ?, ?, ?)",
            (username, password_hash, json.dumps(DEFAULT_GOALS), json.dumps(DEFAULT_PROFILE)),
        )
        conn.commit()
        return cursor.lastrowid


def get_user_by_username(username: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_goals(user_id: int) -> dict:
    user = get_user_by_id(user_id)
    if not user:
        return DEFAULT_GOALS.copy()
    try:
        goals = json.loads(user["goals_json"])
        return {**DEFAULT_GOALS, **goals}
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_GOALS.copy()


def set_user_goals(user_id: int, goals: dict):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET goals_json = ? WHERE id = ?",
            (json.dumps(goals), user_id),
        )
        conn.commit()


def get_user_profile(user_id: int) -> dict:
    user = get_user_by_id(user_id)
    if not user:
        return DEFAULT_PROFILE.copy()
    try:
        profile = json.loads(user["profile_json"])
        return {**DEFAULT_PROFILE, **profile}
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_PROFILE.copy()


def set_user_profile(user_id: int, profile: dict):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET profile_json = ? WHERE id = ?",
            (json.dumps(profile), user_id),
        )
        conn.commit()


# ── Metric helpers (user-scoped) ─────────────────────────────

def insert_metric(data: dict, user_id: int):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO metrics (
                user_id, timestamp, date, weight_kg, calories_kcal, calories_burned_kcal, steps,
                sleep_hours, resting_hr_bpm, workout_type, workout_duration_min
            ) VALUES (
                :user_id, :timestamp, :date, :weight_kg, :calories_kcal, :calories_burned_kcal, :steps,
                :sleep_hours, :resting_hr_bpm, :workout_type, :workout_duration_min
            )
            """,
            {**data, "user_id": user_id},
        )
        conn.commit()


def get_all_metrics(user_id: int):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics WHERE user_id = ? ORDER BY timestamp DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_daily_metrics(days: int = 30, *, user_id: int):
    """Return daily aggregated metrics for the last N days.

    Aggregation: MAX for steps/calories (Tasker may report multiple times),
    AVG for weight/sleep/HR. Only non-null values contribute.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                date,
                AVG(weight_kg) AS weight_kg,
                MAX(calories_kcal) AS calories_kcal,
                MAX(calories_burned_kcal) AS calories_burned_kcal,
                MAX(steps) AS steps,
                AVG(sleep_hours) AS sleep_hours,
                AVG(resting_hr_bpm) AS resting_hr_bpm
            FROM metrics
            WHERE user_id = :user_id AND date >= date('now', :offset)
            GROUP BY date
            ORDER BY date ASC
            """,
            {"user_id": user_id, "offset": f"-{days} days"},
        ).fetchall()
        return [dict(row) for row in rows]


def insert_workout(user_id: int, date: str, workout_type: str, duration_min: int, calories_burned: int = None):
    """Insert a workout into the workouts table."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO workouts (user_id, date, workout_type, duration_min, calories_burned)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, date, workout_type, duration_min, calories_burned),
        )
        conn.commit()


def get_workouts(days: int = 7, *, user_id: int):
    """Return recent workout entries grouped by date with totals."""
    with get_connection() as conn:
        # Get all individual workouts
        rows = conn.execute(
            """
            SELECT date, workout_type, duration_min, calories_burned
            FROM workouts
            WHERE user_id = :user_id AND date >= date('now', :offset)
            ORDER BY date DESC, workout_type ASC
            """,
            {"user_id": user_id, "offset": f"-{days} days"},
        ).fetchall()
        
        # Group by date
        workouts_by_date = {}
        for row in rows:
            date = row[0]
            if date not in workouts_by_date:
                workouts_by_date[date] = []
            workouts_by_date[date].append({
                "workout_type": row[1],
                "workout_duration_min": row[2],
                "calories_burned": row[3],
            })
        
        # Format as list with totals
        result = []
        for date in sorted(workouts_by_date.keys(), reverse=True):
            workouts = workouts_by_date[date]
            total_duration = sum(w["workout_duration_min"] for w in workouts)
            total_calories = sum(w["calories_burned"] or 0 for w in workouts)
            result.append({
                "date": date,
                "workouts": workouts,
                "total_duration_min": total_duration,
                "total_calories_burned": total_calories if total_calories > 0 else None,
            })
        
        return result


def get_workouts_for_date_range(start_date: str, end_date: str, *, user_id: int):
    """Get all workouts for a date range, grouped by date."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT date, workout_type, duration_min, calories_burned
            FROM workouts
            WHERE user_id = :user_id AND date >= :start_date AND date <= :end_date
            ORDER BY date ASC, workout_type ASC
            """,
            {"user_id": user_id, "start_date": start_date, "end_date": end_date},
        ).fetchall()
        
        workouts_by_date = {}
        for row in rows:
            date = row[0]
            if date not in workouts_by_date:
                workouts_by_date[date] = []
            workouts_by_date[date].append({
                "workout_type": row[1],
                "duration_min": row[2],
                "calories_burned": row[3],
            })
        
        return workouts_by_date


def get_dates_with_data(user_id: int) -> list[str]:
    """Return a sorted list of all dates that have at least one metric row."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM metrics WHERE user_id = ? ORDER BY date ASC",
            (user_id,),
        ).fetchall()
        return [row[0] for row in rows]


def insert_metrics_batch(rows: list[dict], user_id: int):
    """Insert multiple metric rows in a single transaction."""
    tagged = [{**r, "user_id": user_id} for r in rows]
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO metrics (
                user_id, timestamp, date, weight_kg, calories_kcal, calories_burned_kcal, steps,
                sleep_hours, resting_hr_bpm, workout_type, workout_duration_min
            ) VALUES (
                :user_id, :timestamp, :date, :weight_kg, :calories_kcal, :calories_burned_kcal, :steps,
                :sleep_hours, :resting_hr_bpm, :workout_type, :workout_duration_min
            )
            """,
            tagged,
        )
        conn.commit()
