import os
import json
from pathlib import Path

from fastapi import FastAPI, Depends, Request, Query, UploadFile, File, HTTPException, status
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from datetime import datetime, timezone

from models import IngestPayload, AuthRequest
from auth import get_current_user, hash_password, verify_password, create_access_token
from database import (
    init_db, insert_metric, insert_metrics_batch,
    get_all_metrics, get_daily_metrics, get_workouts, get_dates_with_data,
    create_user, get_user_by_username,
    get_user_goals, set_user_goals,
    get_user_profile, set_user_profile,
    insert_workout, get_workouts_for_date_range,
)

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

# Health Connect Exercise Type Constants Mapping
# Based on androidx.health.connect.client.records.ExerciseSessionRecord.ExerciseType
EXERCISE_TYPE_MAP = {
    0: "OTHER_WORKOUT",
    1: "BADMINTON",
    2: "BASEBALL",
    3: "BASKETBALL",
    4: "BIKING",
    5: "BIKING_STATIONARY",
    6: "BOOT_CAMP",
    7: "BOXING",
    8: "CALISTHENICS",
    9: "CRICKET",
    10: "CROSS_COUNTRY_SKIING",
    11: "CURLING",
    12: "DANCING",
    13: "DIVING",
    14: "ELEVATOR",
    15: "ELLIPTICAL",
    16: "ERGOMETER",
    17: "ESCALATOR",
    18: "FENCING",
    19: "FOOTBALL_AMERICAN",
    20: "FOOTBALL_AUSTRALIAN",
    21: "FOOTBALL_SOCCER",
    22: "FRISBEE_DISC",
    23: "GOLF",
    24: "GUIDED_BREATHING",
    25: "GYMNASTICS",
    26: "HANDBALL",
    27: "HIGH_INTENSITY_INTERVAL_TRAINING",
    28: "HIKING",
    29: "ICE_HOCKEY",
    30: "ICE_SKATING",
    31: "MARTIAL_ARTS",
    32: "PADDLING",
    33: "PARAGLIDING",
    34: "PILATES",
    35: "RACQUETBALL",
    36: "ROCK_CLIMBING",
    37: "ROWING",
    38: "ROWING_MACHINE",
    39: "RUGBY",
    40: "RUNNING",
    41: "RUNNING_JOGGING",
    42: "RUNNING_SAND",
    43: "RUNNING_TREADMILL",
    44: "SAILING",
    45: "SCUBA_DIVING",
    46: "SKATEBOARDING",
    47: "SKATING",
    48: "SKIING",
    49: "SNOWBOARDING",
    50: "SNOWSHOEING",
    51: "SOFTBALL",
    52: "SQUASH",
    53: "STAIR_CLIMBING",
    54: "STAIR_CLIMBING_MACHINE",
    55: "STANDUP_PADDLEBOARDING",
    56: "STRENGTH_TRAINING",
    57: "STRETCHING",
    58: "SURFING",
    59: "SWIMMING",
    60: "SWIMMING_OPEN_WATER",
    61: "SWIMMING_POOL",
    62: "TABLE_TENNIS",
    63: "TENNIS",
    64: "VOLLEYBALL",
    65: "VOLLEYBALL_BEACH",
    66: "VOLLEYBALL_INDOOR",
    67: "WAKEBOARDING",
    68: "WALKING",
    69: "WALKING_FITNESS",
    70: "WALKING_NORDIC",
    71: "WALKING_TREADMILL",
    72: "WATER_POLO",
    73: "WEIGHTLIFTING",
    74: "WHEELCHAIR",
    75: "WIND_SURFING",
    76: "YOGA",
    77: "CROSSFIT",
    78: "KICKBOXING",
    79: "KAYAKING",
    80: "KITESURFING",
    81: "PICKLEBALL",
    82: "WALL_CLIMBING",
    83: "WATER_FITNESS",
    84: "WATER_SKIING",
    85: "WATER_THERAPY",
    86: "WRESTLING",
}


def map_exercise_type(exercise_type_raw) -> str:
    """Convert Health Connect exercise type integer to human-readable string."""
    if exercise_type_raw is None:
        return "Exercise"
    
    # Try to convert to int if it's a string
    try:
        exercise_type_int = int(exercise_type_raw)
        mapped = EXERCISE_TYPE_MAP.get(exercise_type_int, f"Exercise_{exercise_type_int}")
        # Format: "RUNNING" -> "Running", "HIGH_INTENSITY_INTERVAL_TRAINING" -> "High Intensity Interval Training"
        return format_exercise_name(mapped)
    except (ValueError, TypeError):
        # If it's already a string, format it
        return format_exercise_name(str(exercise_type_raw))


def format_exercise_name(name: str) -> str:
    """Format exercise type name to be more readable.
    
    Examples:
        "RUNNING" -> "Running"
        "HIGH_INTENSITY_INTERVAL_TRAINING" -> "High Intensity Interval Training"
        "BIKING_STATIONARY" -> "Biking Stationary"
    """
    if not name:
        return "Exercise"
    
    # Replace underscores with spaces and title case
    formatted = name.replace("_", " ").title()
    return formatted


def infer_exercise_type_from_title(title: str, default_type: str) -> str:
    """Infer exercise type from title when it clearly indicates a different activity.
    
    This helps correct cases where Health Connect's exercise_type integer doesn't match
    the actual activity described in the title. Only overrides when title is very specific.
    """
    if not title:
        return default_type
    
    title_lower = title.lower()
    
    # Swimming-related terms (multiple languages) - be specific, avoid generic terms
    swimming_terms = [
        "swim", "swimming", "natation", "nado", "plivanje",
        "backstroke", "breaststroke", "butterfly", "pool", "piscina", "bazen"
    ]
    # Only match if it's clearly swimming (not just "freestyle" which is generic)
    if any(term in title_lower for term in swimming_terms):
        return "Swimming"
    
    # Running-related terms
    running_terms = ["run", "running", "jog", "jogging", "correr", "trčanje", "trcanje"]
    if any(term in title_lower for term in running_terms):
        return "Running"
    
    # Cycling/Biking terms
    cycling_terms = ["bike", "biking", "cycle", "cycling", "bicikl", "bicikla", "vélo", "vožnja bicikla"]
    if any(term in title_lower for term in cycling_terms):
        return "Biking"
    
    # Strength training
    strength_terms = ["strength", "weight", "weights", "gym", "sila", "snaga", "trening"]
    if any(term in title_lower for term in strength_terms):
        return "Strength Training"
    
    # Yoga
    yoga_terms = ["yoga", "joga"]
    if any(term in title_lower for term in yoga_terms):
        return "Yoga"
    
    # Walking
    walking_terms = ["walk", "walking", "hodanje", "marche"]
    if any(term in title_lower for term in walking_terms):
        return "Walking"
    
    # Generic exercise terms - keep default type, don't override
    generic_terms = ["exercise", "vježbanje", "workout", "training", "freestyle"]
    if any(term in title_lower for term in generic_terms):
        # Generic terms don't indicate a specific activity, keep the mapped type
        return default_type
    
    # If no match, return the default (mapped type)
    return default_type

# Resolve frontend directory
# In Docker: backend files are at /app/, frontend at /app/frontend/
# In dev: backend/ and frontend/ are siblings under the project root
_this_dir = Path(__file__).resolve().parent
FRONTEND_DIR = _this_dir / "frontend" if (_this_dir / "frontend").exists() else _this_dir.parent / "frontend"


@app.on_event("startup")
def startup():
    init_db()


# --- Dashboard (unauthenticated — HTML is static) ---

@app.get("/")
def serve_dashboard():
    """Serve the dashboard HTML file."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return {"error": "Dashboard not found. Create frontend/index.html."}


# --- Auth endpoints (unauthenticated) ---

@app.post("/api/auth/register")
def register(body: AuthRequest):
    """Create a new user account."""
    existing = get_user_by_username(body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    hashed = hash_password(body.password)
    user_id = create_user(body.username, hashed)
    token = create_access_token(user_id)
    return {"status": "ok", "token": token, "username": body.username}


@app.post("/api/auth/login")
def login(body: AuthRequest):
    """Authenticate and return a JWT token."""
    user = get_user_by_username(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(user["id"])
    return {"status": "ok", "token": token, "username": user["username"]}


# --- Ingest endpoints ---

@app.post("/api/ingest")
def ingest(payload: IngestPayload, user: dict = Depends(get_current_user)):
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
    insert_metric(data, user_id=user["id"])
    return {"status": "ok"}


@app.post("/api/ingest/batch")
def ingest_batch(payloads: list[IngestPayload], user: dict = Depends(get_current_user)):
    """Ingest multiple metric records in a single request.

    Used by the Android app to efficiently upload many days at once.
    """
    rows = []
    for payload in payloads:
        date = payload.timestamp[:10]
        rows.append({
            "timestamp": payload.timestamp,
            "date": date,
            "weight_kg": payload.weight_kg,
            "calories_kcal": payload.calories_kcal,
            "calories_burned_kcal": None,  # Not available from ingest API
            "steps": payload.steps,
            "sleep_hours": payload.sleep_hours,
            "resting_hr_bpm": payload.resting_hr_bpm,
            "workout_type": payload.workout_type,
            "workout_duration_min": payload.workout_duration_min,
        })
    if rows:
        insert_metrics_batch(rows, user_id=user["id"])
    return {"status": "ok", "inserted": len(rows)}


@app.get("/api/data/dates")
def get_data_dates(user: dict = Depends(get_current_user)):
    """Return all dates that have data. Used by the app to decide what to sync."""
    return get_dates_with_data(user_id=user["id"])


@app.post("/api/health-connect/{metric}")
async def ingest_health_connect(metric: str, request: Request, user: dict = Depends(get_current_user)):
    """
    Accept raw TaskerHealthConnect plugin output for a single aggregate metric.

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
        "calories_burned_kcal": None,
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

    insert_metric(data, user_id=user["id"])
    return {"status": "ok", "metric": metric, "value": data[column]}


# --- Read endpoints ---

@app.get("/api/data")
def get_data(user: dict = Depends(get_current_user)):
    return get_all_metrics(user_id=user["id"])


@app.get("/api/data/daily")
def get_data_daily(days: int = Query(default=30, ge=1, le=365), user: dict = Depends(get_current_user)):
    """Return daily aggregated metrics for the last N days."""
    return get_daily_metrics(days, user_id=user["id"])


@app.get("/api/data/workouts")
def get_data_workouts(days: int = Query(default=30, ge=1, le=365), user: dict = Depends(get_current_user)):
    """Return recent workout entries."""
    return get_workouts(days, user_id=user["id"])


# --- Goals config (per-user, stored in DB) ---

@app.get("/api/goals")
def get_goals(user: dict = Depends(get_current_user)):
    """Return current goal settings."""
    return get_user_goals(user["id"])


@app.post("/api/goals")
async def set_goals(request: Request, user: dict = Depends(get_current_user)):
    """Update goal settings. Accepts partial updates."""
    body = await request.json()
    goals = get_user_goals(user["id"])
    for key in ("steps", "calories", "sleep", "target_weight_kg"):
        if key in body:
            goals[key] = body[key]
    set_user_goals(user["id"], goals)
    return {"status": "ok", "goals": goals}


# --- Profile config (per-user, stored in DB) ---

@app.get("/api/profile")
def get_profile(user: dict = Depends(get_current_user)):
    """Return profile / body measurements."""
    return get_user_profile(user["id"])


@app.post("/api/profile")
async def set_profile(request: Request, user: dict = Depends(get_current_user)):
    """Update profile measurements. Accepts partial updates."""
    body = await request.json()
    profile = get_user_profile(user["id"])
    for key in ("height_cm", "age", "sex"):
        if key in body:
            profile[key] = body[key]
    set_user_profile(user["id"], profile)
    return {"status": "ok", "profile": profile}


# --- Health Connect DB Import ---

IMPORT_TIMESTAMP_MARKER = "T12:00:00+00:00"


@app.post("/api/import/health-connect")
async def import_health_connect_db(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Upload a Health Connect export .db file and import its data.

    Deduplication: any previously imported rows (identified by the
    T12:00:00+00:00 timestamp marker) are replaced on re-import.
    Tasker-generated rows (with real timestamps) are never touched.
    """
    import sqlite3
    import tempfile
    from datetime import date as dt_date, timedelta

    user_id = user["id"]

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

        # Calories consumed (cal -> kcal) from nutrition
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

        # Total calories burned (cal -> kcal) from total_calories_burned_record_table
        if "total_calories_burned_record_table" in tables:
            for row in hc.execute(
                "SELECT local_date, SUM(energy) FROM total_calories_burned_record_table "
                "WHERE energy IS NOT NULL GROUP BY local_date"
            ):
                d = epoch_days_to_date(row[0])
                kcal = int(round(row[1] / 1000.0))
                if kcal > 0:
                    ensure_day(d)
                    data_by_day[d]["calories_burned_kcal"] = kcal

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

        # Workouts/Exercise sessions - store individually
        workouts_to_import = []
        
        if "exercise_session_record_table" in tables:
            try:
                # Get title field if available
                workout_columns = [col[1] for col in hc.execute("PRAGMA table_info(exercise_session_record_table)").fetchall()]
                has_title = "title" in workout_columns
                
                query = """
                    SELECT local_date, exercise_type, (end_time - start_time) as duration_ms
                """
                if has_title:
                    query += ", title"
                else:
                    query += ", NULL as title"
                query += """
                    FROM exercise_session_record_table
                    WHERE end_time > start_time
                """
                
                for row in hc.execute(query):
                    d = epoch_days_to_date(row[0])
                    exercise_type_raw = row[1]
                    duration_ms = row[2]
                    title = row[3] if len(row) > 3 else None
                    
                    if duration_ms and duration_ms > 0:
                        duration_min = int(round(duration_ms / 60_000))
                        if duration_min > 0:
                            # Map exercise type to readable string
                            workout_type = map_exercise_type(exercise_type_raw)
                            
                            # Override with title-based inference if title suggests different activity
                            if title:
                                inferred_type = infer_exercise_type_from_title(title, workout_type)
                                if inferred_type != workout_type:
                                    workout_type = inferred_type
                            
                            # Health Connect doesn't provide per-workout calories in exports
                            # total_calories_burned_record_table contains TOTAL daily expenditure
                            # (BMR + steps + workouts + everything), not just workout calories
                            # So we leave calories_burned as None to avoid double-counting
                            calories_burned = None
                            
                            workouts_to_import.append({
                                "date": d,
                                "workout_type": workout_type,
                                "duration_min": duration_min,
                                "calories_burned": calories_burned,
                            })
            except sqlite3.OperationalError as e:
                # Table exists but might have different schema - skip workouts
                pass

        hc.close()

        if not data_by_day:
            os.unlink(tmp_path)
            return {"status": "ok", "message": "No data found in export",
                    "imported": 0, "replaced": 0}

        # ── Write to backend DB ──────────────────────────────────────
        from database import get_connection

        with get_connection() as conn:
            # Find which import dates already exist (by marker timestamp) for this user
            all_dates = sorted(data_by_day.keys())
            marker_timestamps = [f"{d}{IMPORT_TIMESTAMP_MARKER}" for d in all_dates]

            existing_import = set(
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT date FROM metrics "
                    f"WHERE user_id = ? AND timestamp IN ({','.join('?' for _ in marker_timestamps)})",
                    [user_id] + marker_timestamps,
                ).fetchall()
            )

            # Delete old import rows for dates we're about to re-import
            replaced = 0
            if existing_import:
                del_markers = [f"{d}{IMPORT_TIMESTAMP_MARKER}" for d in existing_import]
                del_placeholders = ",".join("?" for _ in del_markers)
                cursor = conn.execute(
                    f"DELETE FROM metrics WHERE user_id = ? AND timestamp IN ({del_placeholders})",
                    [user_id] + del_markers,
                )
                replaced = cursor.rowcount

            # Delete old workouts for dates we're about to re-import
            workout_dates = set(w["date"] for w in workouts_to_import)
            if workout_dates:
                workout_placeholders = ",".join("?" for _ in workout_dates)
                conn.execute(
                    f"DELETE FROM workouts WHERE user_id = ? AND date IN ({workout_placeholders})",
                    [user_id] + list(workout_dates),
                )

            # Insert fresh metric rows
            imported = 0
            for d in all_dates:
                day = data_by_day[d]
                if not day:
                    continue
                conn.execute(
                    """INSERT INTO metrics
                       (user_id, timestamp, date, weight_kg, calories_kcal, calories_burned_kcal, steps,
                        sleep_hours, resting_hr_bpm, workout_type, workout_duration_min)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                    (
                        user_id,
                        f"{d}{IMPORT_TIMESTAMP_MARKER}",
                        d,
                        day.get("weight_kg"),
                        day.get("calories_kcal"),
                        day.get("calories_burned_kcal"),
                        day.get("steps"),
                        day.get("sleep_hours"),
                        day.get("resting_hr_bpm"),
                    ),
                )
                imported += 1

            # Insert individual workouts
            workouts_imported = 0
            for workout in workouts_to_import:
                conn.execute(
                    """INSERT INTO workouts (user_id, date, workout_type, duration_min, calories_burned)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        workout["date"],
                        workout["workout_type"],
                        workout["duration_min"],
                        workout["calories_burned"],
                    ),
                )
                workouts_imported += 1

            conn.commit()

        os.unlink(tmp_path)

        workout_dates = set(w["date"] for w in workouts_to_import)
        return {
            "status": "ok",
            "imported": imported,
            "replaced": replaced,
            "workouts_imported": workouts_imported,
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else None,
            "metrics": {
                "steps": sum(1 for d in data_by_day.values() if "steps" in d),
                "weight": sum(1 for d in data_by_day.values() if "weight_kg" in d),
                "calories": sum(1 for d in data_by_day.values() if "calories_kcal" in d),
                "calories_burned": sum(1 for d in data_by_day.values() if "calories_burned_kcal" in d),
                "sleep": sum(1 for d in data_by_day.values() if "sleep_hours" in d),
                "heart_rate": sum(1 for d in data_by_day.values() if "resting_hr_bpm" in d),
                "workouts": len(workout_dates),
                "workout_sessions": workouts_imported,
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
    """Estimate net calories burned from walking using distance-based formula."""
    stride_m = height_cm * 0.414 / 100
    distance_km = steps * stride_m / 1000
    return 0.5 * weight_kg * distance_km


@app.get("/api/data/deficit")
def get_deficit(days: int = Query(default=30, ge=1, le=365), user: dict = Depends(get_current_user)):
    """Calculate TDEE and calorie deficit/surplus for each day."""
    goals = get_user_goals(user["id"])
    profile = get_user_profile(user["id"])
    height_cm = profile.get("height_cm", 175)
    age = profile.get("age", 30)
    sex = profile.get("sex", "male")
    target_weight_kg = goals.get("target_weight_kg")

    daily = get_daily_metrics(days, user_id=user["id"])
    
    # Get workouts for the date range
    if daily:
        start_date = daily[0]["date"]
        end_date = daily[-1]["date"]
        workouts_by_date = get_workouts_for_date_range(start_date, end_date, user_id=user["id"])
    else:
        workouts_by_date = {}

    # Build weight lookup for extrapolation
    # Collect all known weights with their dates
    known_weights = {}
    for day in daily:
        if day.get("weight_kg") is not None:
            known_weights[day["date"]] = day["weight_kg"]
    
    def get_extrapolated_weight(date: str) -> float | None:
        """Get weight for a date, using extrapolation if missing.
        
        Strategy:
        1. If weight exists for this date, use it
        2. Otherwise, use the most recent known weight before this date (forward fill)
        3. If no weight before, use the first known weight after (backward fill)
        """
        if date in known_weights:
            return known_weights[date]
        
        # Find most recent weight before this date
        for known_date in sorted(known_weights.keys(), reverse=True):
            if known_date < date:
                return known_weights[known_date]
        
        # Find first weight after this date (backward fill)
        for known_date in sorted(known_weights.keys()):
            if known_date > date:
                return known_weights[known_date]
        
        return None

    daily_breakdown = []
    deficits = []

    for day in daily:
        entry = {"date": day["date"]}
        weight = day.get("weight_kg")
        steps = day.get("steps")
        calories = day.get("calories_kcal")

        # Use extrapolated weight if missing
        if weight is None:
            weight = get_extrapolated_weight(day["date"])
            if weight is not None:
                entry["weight_extrapolated"] = True
            else:
                entry["weight_extrapolated"] = False
        else:
            entry["weight_extrapolated"] = False

        if weight is not None:
            entry["weight_kg"] = round(weight, 1)
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

            # Workout calories for this day
            # Note: Health Connect doesn't provide per-workout calories in exports,
            # so workout_calories will be 0. Workout calories are not included in TDEE
            # to avoid double-counting (total_calories_burned already includes everything).
            workout_calories = 0.0
            day_workouts = workouts_by_date.get(day["date"], [])
            if day_workouts:
                # Sum calories from all workouts for this day (will be 0 since calories_burned is None)
                workout_calories = sum(w.get("calories_burned") or 0 for w in day_workouts)
                entry["workouts"] = day_workouts
                entry["workout_calories"] = round(workout_calories) if workout_calories > 0 else None
            else:
                entry["workouts"] = []
                entry["workout_calories"] = None

            # TEF — use actual food intake if available, else estimate from BMR
            if calories is not None:
                tef = 0.1 * calories
            else:
                tef = 0.1 * bmr
            entry["tef"] = round(tef)

            # TDEE = BMR + Walking + TEF + NEAT
            # Note: Workout calories are NOT added here because:
            # 1. Health Connect doesn't provide per-workout calories in exports
            # 2. total_calories_burned_record_table already includes workout calories in the total
            # 3. Adding them would cause double-counting
            tdee = bmr + walking + tef + neat
            entry["tdee"] = round(tdee)

            # Always include calories_consumed (null if missing) for chart consistency
            if calories is not None:
                entry["calories_consumed"] = calories
                deficit = tdee - calories
                entry["deficit"] = round(deficit)
                entry["weekly_kg_change"] = round(deficit * 7 / 7700, 2)
                deficits.append(deficit)
            else:
                entry["calories_consumed"] = None

        daily_breakdown.append(entry)

    # Summary
    summary = {}
    if deficits:
        avg_deficit = sum(deficits) / len(deficits)
        summary["avg_deficit"] = round(avg_deficit)
        summary["avg_weekly_kg_change"] = round(avg_deficit * 7 / 7700, 2)

        # Time to target
        if target_weight_kg is not None:
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
