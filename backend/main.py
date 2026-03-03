import os
import json
from pathlib import Path

from fastapi import FastAPI, Request, Query, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from datetime import datetime, timezone

from models import IngestPayload
from database import init_db, insert_metric, get_all_metrics, get_daily_metrics, get_workouts

load_dotenv()

app = FastAPI(title="Health Dashboard API")

# Maps URL metric names to database column names
METRIC_MAP = {
    "steps": "steps",
    "weight": "weight_kg",
    "calories": "calories_kcal",
    "sleep": "sleep_hours",
    "heartrate": "resting_hr_bpm",
}

# Goals config file path (persisted alongside the DB)
_data_dir = Path(os.getenv("DB_PATH", "./data/health.db")).resolve().parent
GOALS_FILE = _data_dir / "goals.json"
DEFAULT_GOALS = {
    "steps": 10000,
    "calories": 2000,
    "sleep": 7,
    "height_cm": 175,
    "age": 30,
    "sex": "male",
    "target_weight_kg": None,
}


def _load_goals():
    if GOALS_FILE.exists():
        try:
            return json.loads(GOALS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_GOALS.copy()


def _save_goals(goals: dict):
    GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOALS_FILE.write_text(json.dumps(goals, indent=2))

# Resolve frontend directory
# In Docker: backend files are at /app/, frontend at /app/frontend/
# In dev: backend/ and frontend/ are siblings under the project root
_this_dir = Path(__file__).resolve().parent
FRONTEND_DIR = _this_dir / "frontend" if (_this_dir / "frontend").exists() else _this_dir.parent / "frontend"


@app.on_event("startup")
def startup():
    init_db()


# --- Dashboard ---

@app.get("/")
def serve_dashboard():
    """Serve the dashboard HTML file."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "Dashboard not found. Create frontend/index.html."}


# --- Ingest endpoints ---

@app.post("/api/ingest")
def ingest(payload: IngestPayload):
    date = payload.timestamp[:10]
    data = {
        "timestamp": payload.timestamp,
        "date": date,
        "weight_kg": payload.weight_kg,
        "calories_kcal": payload.calories_kcal,
        "steps": payload.steps,
        "sleep_hours": payload.sleep_hours,
        "resting_hr_bpm": payload.resting_hr_bpm,
        "workout_type": payload.workout_type,
        "workout_duration_min": payload.workout_duration_min,
    }
    insert_metric(data)
    return {"status": "ok"}


@app.post("/api/health-connect/{metric}")
async def ingest_health_connect(metric: str, request: Request):
    """
    Accept raw TaskerHealthConnect plugin output for a single aggregate metric.

    Tasker config per metric:
      Action 1: Plugin -> Tasker Health Connect -> Read Aggregated Data
                metric: e.g. StepsRecord.COUNT_TOTAL
      Action 2: HTTP POST to /api/health-connect/steps
                body: %healthconnectresult

    Supported metric paths: steps, weight, calories, sleep, heartrate
    """
    if metric not in METRIC_MAP:
        return {"status": "error", "message": f"Unknown metric '{metric}'. Use one of: {list(METRIC_MAP.keys())}"}

    raw_body = (await request.body()).decode("utf-8").strip()

    value = _extract_value(raw_body)
    if value is None:
        return {"status": "error", "message": f"Could not extract a number from body: {raw_body[:200]}"}

    now = datetime.now(tz=timezone.utc)
    column = METRIC_MAP[metric]

    data = {
        "timestamp": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "weight_kg": None,
        "calories_kcal": None,
        "steps": None,
        "sleep_hours": None,
        "resting_hr_bpm": None,
        "workout_type": None,
        "workout_duration_min": None,
    }

    if column in ("steps", "calories_kcal", "resting_hr_bpm"):
        data[column] = int(value)
    else:
        data[column] = float(value)

    insert_metric(data)
    return {"status": "ok", "metric": metric, "value": data[column]}


# --- Read endpoints ---

@app.get("/api/data")
def get_data():
    return get_all_metrics()


@app.get("/api/data/daily")
def get_data_daily(days: int = Query(default=30, ge=1, le=365)):
    """Return daily aggregated metrics for the last N days."""
    return get_daily_metrics(days)


@app.get("/api/data/workouts")
def get_data_workouts(days: int = Query(default=30, ge=1, le=365)):
    """Return recent workout entries."""
    return get_workouts(days)


# --- Goals config ---

@app.get("/api/goals")
def get_goals():
    """Return current goal settings."""
    return _load_goals()


@app.post("/api/goals")
async def set_goals(request: Request):
    """Update goal settings. Accepts partial updates."""
    body = await request.json()
    goals = _load_goals()
    for key in ("steps", "calories", "sleep", "height_cm", "age", "sex", "target_weight_kg"):
        if key in body:
            goals[key] = body[key]
    _save_goals(goals)
    return {"status": "ok", "goals": goals}


# --- Health Connect DB Import ---

IMPORT_TIMESTAMP_MARKER = "T12:00:00+00:00"


@app.post("/api/import/health-connect")
async def import_health_connect_db(file: UploadFile = File(...)):
    """Upload a Health Connect export .db file and import its data.

    Deduplication: any previously imported rows (identified by the
    T12:00:00+00:00 timestamp marker) are replaced on re-import.
    Tasker-generated rows (with real timestamps) are never touched.
    """
    import sqlite3
    import tempfile
    from datetime import date as dt_date, timedelta

    # Save uploaded file to a temp location
    contents = await file.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.write(contents)
    tmp.close()
    tmp_path = tmp.name

    try:
        hc = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)

        # Verify it looks like a Health Connect export
        tables = [r[0] for r in hc.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]

        expected = {"steps_record_table", "weight_record_table",
                    "nutrition_record_table"}
        if not expected & set(tables):
            hc.close()
            os.unlink(tmp_path)
            return {"status": "error",
                    "message": f"Not a Health Connect export. Found tables: {tables}"}

        def epoch_days_to_date(days: int) -> str:
            return (dt_date(1970, 1, 1) + timedelta(days=days)).isoformat()

        # ── Aggregate from HC export ─────────────────────────────────
        data_by_day: dict[str, dict] = {}

        def ensure_day(d: str):
            if d not in data_by_day:
                data_by_day[d] = {}

        # Steps
        if "steps_record_table" in tables:
            for row in hc.execute(
                "SELECT local_date, SUM(count) FROM steps_record_table GROUP BY local_date"
            ):
                d = epoch_days_to_date(row[0])
                ensure_day(d)
                data_by_day[d]["steps"] = int(row[1])

        # Weight (grams -> kg)
        if "weight_record_table" in tables:
            for row in hc.execute(
                "SELECT local_date, AVG(weight) FROM weight_record_table GROUP BY local_date"
            ):
                d = epoch_days_to_date(row[0])
                ensure_day(d)
                data_by_day[d]["weight_kg"] = round(row[1] / 1000.0, 2)

        # Calories (cal -> kcal)
        if "nutrition_record_table" in tables:
            for row in hc.execute(
                "SELECT local_date, SUM(energy) FROM nutrition_record_table "
                "WHERE energy IS NOT NULL GROUP BY local_date"
            ):
                d = epoch_days_to_date(row[0])
                kcal = int(round(row[1] / 1000.0))
                if kcal > 0:
                    ensure_day(d)
                    data_by_day[d]["calories_kcal"] = kcal

        # Sleep (ms -> hours)
        if "sleep_session_record_table" in tables:
            for row in hc.execute(
                "SELECT local_date, SUM(end_time - start_time) "
                "FROM sleep_session_record_table GROUP BY local_date"
            ):
                d = epoch_days_to_date(row[0])
                hours = round(row[1] / 3_600_000, 2)
                if hours > 0:
                    ensure_day(d)
                    data_by_day[d]["sleep_hours"] = hours

        # Heart rate (min bpm per day)
        if "heart_rate_record_series_table" in tables and "heart_rate_record_table" in tables:
            for row in hc.execute(
                "SELECT r.local_date, MIN(s.beats_per_minute) "
                "FROM heart_rate_record_series_table s "
                "JOIN heart_rate_record_table r ON s.parent_key = r.row_id "
                "WHERE s.beats_per_minute > 30 AND s.beats_per_minute < 220 "
                "GROUP BY r.local_date"
            ):
                d = epoch_days_to_date(row[0])
                ensure_day(d)
                data_by_day[d]["resting_hr_bpm"] = int(row[1])

        hc.close()

        if not data_by_day:
            os.unlink(tmp_path)
            return {"status": "ok", "message": "No data found in export",
                    "imported": 0, "replaced": 0}

        # ── Write to backend DB ──────────────────────────────────────
        from database import get_connection

        with get_connection() as conn:
            # Find which import dates already exist (by marker timestamp)
            all_dates = sorted(data_by_day.keys())
            placeholders = ",".join("?" for _ in all_dates)
            marker_timestamps = [f"{d}{IMPORT_TIMESTAMP_MARKER}" for d in all_dates]

            existing_import = set(
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT date FROM metrics "
                    f"WHERE timestamp IN ({','.join('?' for _ in marker_timestamps)})",
                    marker_timestamps,
                ).fetchall()
            )

            # Delete old import rows for dates we're about to re-import
            replaced = 0
            if existing_import:
                del_markers = [f"{d}{IMPORT_TIMESTAMP_MARKER}" for d in existing_import]
                del_placeholders = ",".join("?" for _ in del_markers)
                cursor = conn.execute(
                    f"DELETE FROM metrics WHERE timestamp IN ({del_placeholders})",
                    del_markers,
                )
                replaced = cursor.rowcount

            # Insert fresh rows
            imported = 0
            for d in all_dates:
                day = data_by_day[d]
                if not day:
                    continue
                conn.execute(
                    """INSERT INTO metrics
                       (timestamp, date, weight_kg, calories_kcal, steps,
                        sleep_hours, resting_hr_bpm, workout_type, workout_duration_min)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                    (
                        f"{d}{IMPORT_TIMESTAMP_MARKER}",
                        d,
                        day.get("weight_kg"),
                        day.get("calories_kcal"),
                        day.get("steps"),
                        day.get("sleep_hours"),
                        day.get("resting_hr_bpm"),
                    ),
                )
                imported += 1

            conn.commit()

        os.unlink(tmp_path)

        return {
            "status": "ok",
            "imported": imported,
            "replaced": replaced,
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else None,
            "metrics": {
                "steps": sum(1 for d in data_by_day.values() if "steps" in d),
                "weight": sum(1 for d in data_by_day.values() if "weight_kg" in d),
                "calories": sum(1 for d in data_by_day.values() if "calories_kcal" in d),
                "sleep": sum(1 for d in data_by_day.values() if "sleep_hours" in d),
                "heart_rate": sum(1 for d in data_by_day.values() if "resting_hr_bpm" in d),
            },
        }

    except sqlite3.Error as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return {"status": "error", "message": f"Database error: {str(e)}"}
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return {"status": "error", "message": f"Import failed: {str(e)}"}


# --- Deficit calculator ---

def _calc_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin-St Jeor BMR equation."""
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age
    if sex == "female":
        bmr -= 161
    else:
        bmr += 5
    return bmr


def _calc_walking_calories(steps: int, weight_kg: float, height_cm: float) -> float:
    """Estimate net calories burned from walking using distance-based formula.

    stride_length = height_cm * 0.414  (ACSM standard)
    distance_km = steps * stride_m / 1000
    net_calories = 0.5 * weight_kg * distance_km  (Margaria's walking energy cost)

    Returns net walking calories (above resting).
    """
    stride_m = height_cm * 0.414 / 100
    distance_km = steps * stride_m / 1000
    return 0.5 * weight_kg * distance_km


@app.get("/api/data/deficit")
def get_deficit(days: int = Query(default=30, ge=1, le=365)):
    """Calculate TDEE and calorie deficit/surplus for each day.

    TDEE = BMR + Walking Calories + TEF + NEAT overhead

    Components:
    - BMR: Mifflin-St Jeor equation (weight, height, age, sex)
    - Walking: Margaria's 0.5 kcal/kg/km using height-based stride length
    - TEF: Thermic Effect of Food = 10% of calories consumed
    - NEAT: Non-Exercise Activity Thermogenesis overhead = 12% of BMR
    """
    goals = _load_goals()
    height_cm = goals.get("height_cm", 175)
    age = goals.get("age", 30)
    sex = goals.get("sex", "male")
    target_weight_kg = goals.get("target_weight_kg")

    daily = get_daily_metrics(days)

    daily_breakdown = []
    deficits = []

    for day in daily:
        entry = {"date": day["date"]}
        weight = day.get("weight_kg")
        steps = day.get("steps")
        calories = day.get("calories_kcal")

        if weight is not None:
            bmr = _calc_bmr(weight, height_cm, age, sex)
            neat = bmr * 0.12
            entry["bmr"] = round(bmr)
            entry["neat"] = round(neat)

            # Walking calories (0 if no step data)
            walking = 0.0
            distance_km = 0.0
            if steps is not None:
                walking = _calc_walking_calories(steps, weight, height_cm)
                stride_m = height_cm * 0.414 / 100
                distance_km = steps * stride_m / 1000
            entry["walking_calories"] = round(walking)
            entry["distance_km"] = round(distance_km, 2)

            # TEF — use actual food intake if available, else estimate from BMR
            if calories is not None:
                tef = 0.1 * calories
            else:
                tef = 0.1 * bmr  # rough estimate when no food data
            entry["tef"] = round(tef)

            # TDEE = BMR + Walking + TEF + NEAT
            tdee = bmr + walking + tef + neat
            entry["tdee"] = round(tdee)

            if calories is not None:
                entry["calories_consumed"] = calories
                deficit = tdee - calories
                entry["deficit"] = round(deficit)
                entry["weekly_kg_change"] = round(deficit * 7 / 7700, 2)
                deficits.append(deficit)

        daily_breakdown.append(entry)

    # Summary
    summary = {}
    if deficits:
        avg_deficit = sum(deficits) / len(deficits)
        summary["avg_deficit"] = round(avg_deficit)
        summary["avg_weekly_kg_change"] = round(avg_deficit * 7 / 7700, 2)

        # Time to target
        if target_weight_kg is not None:
            # Use latest weight
            latest_weight = None
            for day in reversed(daily):
                if day.get("weight_kg") is not None:
                    latest_weight = day["weight_kg"]
                    break

            if latest_weight is not None:
                kg_to_lose = latest_weight - target_weight_kg
                summary["current_weight_kg"] = round(latest_weight, 1)
                summary["target_weight_kg"] = target_weight_kg
                summary["kg_to_lose"] = round(kg_to_lose, 1)

                weekly_change = avg_deficit * 7 / 7700
                if weekly_change > 0 and kg_to_lose > 0:
                    weeks = kg_to_lose / weekly_change
                    summary["estimated_weeks"] = round(weeks, 1)
                elif kg_to_lose <= 0:
                    summary["estimated_weeks"] = 0
                    summary["message"] = "Target already reached!"
                else:
                    summary["message"] = "Currently in surplus — reduce calories or increase activity"

    return {"summary": summary, "daily": daily_breakdown}


# --- Helpers ---

def _extract_value(raw: str):
    """Try to pull a numeric value from the plugin output.

    Handles TaskerHealthConnect format:
    {"dataOrigins":[...],"doubleValues":{},"longValues":{"Steps_count_total":4994}}
    """
    import json

    # Case 1: plain number (e.g. "5660" or "85.4")
    try:
        return float(raw)
    except ValueError:
        pass

    # Case 2: JSON
    try:
        parsed = json.loads(raw)

        if isinstance(parsed, (int, float)):
            return float(parsed)

        if isinstance(parsed, dict):
            # TaskerHealthConnect format: longValues / doubleValues
            for values_key in ("longValues", "doubleValues"):
                if values_key in parsed and isinstance(parsed[values_key], dict):
                    vals = parsed[values_key]
                    nums = [v for v in vals.values() if isinstance(v, (int, float))]
                    if nums:
                        return float(nums[0])

            # Fallback: common value fields
            for key in ("value", "result", "count", "total", "avg"):
                if key in parsed and isinstance(parsed[key], (int, float)):
                    return float(parsed[key])

            # Fallback: single numeric value in dict
            nums = [v for v in parsed.values() if isinstance(v, (int, float))]
            if len(nums) == 1:
                return nums[0]

    except (json.JSONDecodeError, TypeError):
        pass

    return None
