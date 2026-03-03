from fastapi import FastAPI
from dotenv import load_dotenv

from models import IngestPayload
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


@app.get("/api/data")
def get_data():
    return get_all_metrics()
