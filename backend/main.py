import os
import json
from pathlib import Path

from fastapi import FastAPI, Request, Query
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
DEFAULT_GOALS = {"steps": 10000, "calories": 2000, "sleep": 7}


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
    for key in ("steps", "calories", "sleep"):
        if key in body:
            goals[key] = body[key]
    _save_goals(goals)
    return {"status": "ok", "goals": goals}


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
