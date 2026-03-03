#!/usr/bin/env python3
"""
Import historical Health Connect data from the export .db file
into the backend's health.db (metrics table).

Usage:
    python import/import_hc_export.py

Data mapping:
    steps_record_table          → steps        (SUM per day)
    weight_record_table         → weight_kg    (AVG per day, grams → kg)
    nutrition_record_table      → calories_kcal(SUM energy per day, cal → kcal)
    sleep_session_record_table  → sleep_hours  (SUM duration per day, ms → hours)
    heart_rate_record_series    → resting_hr   (MIN bpm per day — approximation)
"""

import sqlite3
import os
import sys
from datetime import date, timedelta

# ── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HC_EXPORT_DB = os.path.join(SCRIPT_DIR, "health_connect_export.db")
BACKEND_DB = os.path.join(SCRIPT_DIR, "..", "data", "health.db")


def epoch_days_to_date(days: int) -> str:
    """Convert Health Connect local_date (days since 1970-01-01) to YYYY-MM-DD."""
    return (date(1970, 1, 1) + timedelta(days=days)).isoformat()


def query_steps(hc: sqlite3.Connection) -> dict:
    """Sum step counts per local_date."""
    result = {}
    for row in hc.execute(
        "SELECT local_date, SUM(count) AS total "
        "FROM steps_record_table GROUP BY local_date"
    ):
        d = epoch_days_to_date(row[0])
        result[d] = int(row[1])
    return result


def query_weight(hc: sqlite3.Connection) -> dict:
    """Average weight per local_date.  Stored in grams → convert to kg."""
    result = {}
    for row in hc.execute(
        "SELECT local_date, AVG(weight) AS avg_w "
        "FROM weight_record_table GROUP BY local_date"
    ):
        d = epoch_days_to_date(row[0])
        result[d] = round(row[1] / 1000.0, 2)  # grams → kg
    return result


def query_calories(hc: sqlite3.Connection) -> dict:
    """Sum nutrition energy per local_date.  Stored in calories → convert to kcal."""
    result = {}
    for row in hc.execute(
        "SELECT local_date, SUM(energy) AS total_e "
        "FROM nutrition_record_table WHERE energy IS NOT NULL GROUP BY local_date"
    ):
        d = epoch_days_to_date(row[0])
        kcal = int(round(row[1] / 1000.0))  # cal → kcal
        if kcal > 0:
            result[d] = kcal
    return result


def query_sleep(hc: sqlite3.Connection) -> dict:
    """Sum sleep session durations per local_date.  ms → hours."""
    result = {}
    for row in hc.execute(
        "SELECT local_date, SUM(end_time - start_time) AS total_ms "
        "FROM sleep_session_record_table GROUP BY local_date"
    ):
        d = epoch_days_to_date(row[0])
        hours = round(row[1] / 3_600_000, 2)
        if hours > 0:
            result[d] = hours
    return result


def query_resting_hr(hc: sqlite3.Connection) -> dict:
    """Estimate resting HR as the minimum beats_per_minute per day.

    Uses heart_rate_record_series_table joined with heart_rate_record_table
    for local_date context.
    """
    result = {}
    for row in hc.execute(
        """
        SELECT r.local_date, MIN(s.beats_per_minute) AS min_hr
        FROM heart_rate_record_series_table s
        JOIN heart_rate_record_table r ON s.parent_key = r.row_id
        WHERE s.beats_per_minute > 30          -- filter out sensor glitches
          AND s.beats_per_minute < 220
        GROUP BY r.local_date
        """
    ):
        d = epoch_days_to_date(row[0])
        result[d] = int(row[1])
    return result


def import_data():
    # ── Validate paths ───────────────────────────────────────────────
    if not os.path.exists(HC_EXPORT_DB):
        print(f"ERROR: Health Connect export not found at {HC_EXPORT_DB}")
        sys.exit(1)

    # ── Open databases ───────────────────────────────────────────────
    hc = sqlite3.connect(f"file:{HC_EXPORT_DB}?mode=ro", uri=True)

    os.makedirs(os.path.dirname(os.path.abspath(BACKEND_DB)), exist_ok=True)
    be = sqlite3.connect(BACKEND_DB)

    # Ensure metrics table exists (mirrors backend/database.py)
    be.execute("""
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
    be.execute("CREATE INDEX IF NOT EXISTS idx_date ON metrics(date)")
    be.commit()

    # ── Dates already present ────────────────────────────────────────
    existing_dates = set(
        row[0] for row in be.execute("SELECT DISTINCT date FROM metrics").fetchall()
    )

    # ── Aggregate from HC export ─────────────────────────────────────
    print("Reading Health Connect export...")
    steps_by_day = query_steps(hc)
    print(f"  Steps:      {len(steps_by_day)} days")

    weight_by_day = query_weight(hc)
    print(f"  Weight:     {len(weight_by_day)} days")

    calories_by_day = query_calories(hc)
    print(f"  Calories:   {len(calories_by_day)} days")

    sleep_by_day = query_sleep(hc)
    print(f"  Sleep:      {len(sleep_by_day)} days")

    hr_by_day = query_resting_hr(hc)
    print(f"  Heart Rate: {len(hr_by_day)} days")

    # ── Merge into a single dict per day ─────────────────────────────
    all_dates = sorted(
        set(steps_by_day)
        | set(weight_by_day)
        | set(calories_by_day)
        | set(sleep_by_day)
        | set(hr_by_day)
    )

    if not all_dates:
        print("\nNo data found in the export. Nothing to import.")
        hc.close()
        be.close()
        return

    # ── Insert ───────────────────────────────────────────────────────
    imported = 0
    skipped = 0

    for d in all_dates:
        if d in existing_dates:
            skipped += 1
            continue

        steps = steps_by_day.get(d)
        weight = weight_by_day.get(d)
        calories = calories_by_day.get(d)
        sleep = sleep_by_day.get(d)
        hr = hr_by_day.get(d)

        # Only insert if there's at least one non-null value
        if not any(v is not None for v in (steps, weight, calories, sleep, hr)):
            continue

        timestamp = f"{d}T12:00:00+00:00"
        be.execute(
            """
            INSERT INTO metrics
                (timestamp, date, weight_kg, calories_kcal, steps,
                 sleep_hours, resting_hr_bpm, workout_type, workout_duration_min)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (timestamp, d, weight, calories, steps, sleep, hr),
        )
        imported += 1

    be.commit()

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Import complete!")
    print(f"  Date range : {all_dates[0]}  to  {all_dates[-1]}")
    print(f"  Days found : {len(all_dates)}")
    print(f"  Imported   : {imported}")
    print(f"  Skipped    : {skipped} (already had data)")
    print(f"{'='*40}")

    # Quick sanity check — print a few sample rows
    print("\nSample imported rows:")
    rows = be.execute(
        "SELECT date, weight_kg, calories_kcal, steps, sleep_hours, resting_hr_bpm "
        "FROM metrics WHERE workout_type IS NULL "
        "ORDER BY date LIMIT 5"
    ).fetchall()
    print(f"  {'Date':<12} {'Weight':>8} {'Cal':>6} {'Steps':>7} {'Sleep':>6} {'HR':>4}")
    for r in rows:
        w = f"{r[1]:.1f}" if r[1] else "   —"
        c = str(r[2]) if r[2] else "  —"
        s = str(r[3]) if r[3] else "   —"
        sl = f"{r[4]:.1f}" if r[4] else "  —"
        hr = str(r[5]) if r[5] else " —"
        print(f"  {r[0]:<12} {w:>8} {c:>6} {s:>7} {sl:>6} {hr:>4}")

    hc.close()
    be.close()


if __name__ == "__main__":
    import_data()
