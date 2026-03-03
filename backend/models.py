from pydantic import BaseModel
from typing import Optional, Dict, Any


class IngestPayload(BaseModel):
    timestamp: str
    weight_kg: Optional[float] = None
    calories_kcal: Optional[int] = None
    steps: Optional[int] = None
    sleep_hours: Optional[float] = None
    resting_hr_bpm: Optional[int] = None
    workout_type: Optional[str] = None
    workout_duration_min: Optional[int] = None


class HealthConnectPayload(BaseModel):
    startTime: str
    endTime: str
    result: Dict[str, Any] = {}
