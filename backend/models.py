from pydantic import BaseModel, Field
from typing import Optional


class IngestPayload(BaseModel):
    timestamp: str
    weight_kg: Optional[float] = None
    calories_kcal: Optional[int] = None
    steps: Optional[int] = None
    sleep_hours: Optional[float] = None
    resting_hr_bpm: Optional[int] = None
    workout_type: Optional[str] = None
    workout_duration_min: Optional[int] = None


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=4, max_length=128)
