from fastapi import FastAPI, Request
from dotenv import load_dotenv
from datetime import datetime, timezone

from models import IngestPayload
from database import init_db, insert_metric, get_all_metrics

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


@app.on_event("startup")
def startup():
    init_db()


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

    # Read the raw body — could be a plain number or JSON
    raw_body = (await request.body()).decode("utf-8").strip()

    # Try to extract a numeric value from whatever the plugin sends
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

    # Cast to int for count-based metrics, float for measurements
    if column in ("steps", "calories_kcal", "resting_hr_bpm"):
        data[column] = int(value)
    else:
        data[column] = float(value)

    insert_metric(data)
    return {"status": "ok", "metric": metric, "value": data[column]}


def _extract_value(raw: str):
    """Try to pull a numeric value from the plugin output."""
    import json

    # Case 1: plain number (e.g. "5660" or "85.4")
    try:
        return float(raw)
    except ValueError:
        pass

    # Case 2: JSON — try to find a numeric value
    try:
        parsed = json.loads(raw)

        # If it's just a number wrapped in JSON
        if isinstance(parsed, (int, float)):
            return float(parsed)

        # If it's a dict, look for common value fields
        if isinstance(parsed, dict):
            for key in ("value", "result", "count", "total", "avg"):
                if key in parsed and isinstance(parsed[key], (int, float)):
                    return float(parsed[key])
            # If there's only one numeric value in the dict, use it
            nums = [v for v in parsed.values() if isinstance(v, (int, float))]
            if len(nums) == 1:
                return nums[0]

        # If it's a list with one element
        if isinstance(parsed, list) and len(parsed) == 1:
            item = parsed[0]
            if isinstance(item, (int, float)):
                return float(item)
            if isinstance(item, dict):
                for key in ("value", "result", "count", "total", "avg"):
                    if key in item and isinstance(item[key], (int, float)):
                        return float(item[key])

    except (json.JSONDecodeError, TypeError):
        pass

    return None


@app.get("/api/data")
def get_data():
    return get_all_metrics()
