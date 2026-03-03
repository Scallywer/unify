from fastapi import FastAPI
from dotenv import load_dotenv
from datetime import datetime

from models import IngestPayload, HealthConnectPayload
from database import init_db, insert_metric, get_all_metrics

load_dotenv()

app = FastAPI(title="Health Dashboard API")


@app.on_event("startup")
def startup():
    init_db()


@app.post("/api/ingest")
def ingest(payload: IngestPayload):
    # Extract date from timestamp (YYYY-MM-DD)
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


@app.post("/api/health-connect")
def ingest_health_connect(payload: HealthConnectPayload):
    """Accept raw TaskerHealthConnect plugin output and map to our schema."""
    r = payload.result

    # Use endTime as the timestamp (end of the measurement window)
    timestamp = payload.endTime
    # Extract date (YYYY-MM-DD) from ISO timestamp
    try:
        date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        date = timestamp[:10]

    data = {
        "timestamp": timestamp,
        "date": date,
        "weight_kg": r.get("Weight_weight_avg"),
        "calories_kcal": int(r["Nutrition_calories_total"]) if r.get("Nutrition_calories_total") is not None else None,
        "steps": int(r["Steps_count_total"]) if r.get("Steps_count_total") is not None else None,
        "sleep_hours": r.get("Sleep_duration_total"),
        "resting_hr_bpm": int(r["HeartRate_bpm_avg"]) if r.get("HeartRate_bpm_avg") is not None else None,
        "workout_type": r.get("ExerciseSession_type"),
        "workout_duration_min": r.get("ExerciseSession_duration_total"),
    }
    insert_metric(data)
    return {"status": "ok", "mapped": {k: v for k, v in data.items() if v is not None}}


@app.get("/api/data")
def get_data():
    return get_all_metrics()
