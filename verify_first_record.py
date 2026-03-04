import sqlite3
import os
from datetime import date, timedelta, datetime, timezone

def epoch_days_to_date(days: int) -> str:
    return (date(1970, 1, 1) + timedelta(days=days)).isoformat()

def epoch_ms_to_datetime(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

db_path = 'import/health_connect_export_Nata.db'
conn = sqlite3.connect(db_path)

# Get today's date (March 4, 2026)
target_date = (date(2026, 3, 4) - date(1970, 1, 1)).days
target_date_str = "2026-03-04"

print("=" * 80)
print(f"VERIFYING FIRST RECORD HYPOTHESIS FOR {target_date_str}")
print("=" * 80)

# Get all bike activities
bike_activities = conn.execute("""
    SELECT row_id, exercise_type, start_time, end_time, title, notes,
           (end_time - start_time) as duration_ms
    FROM exercise_session_record_table
    WHERE local_date = ? AND exercise_type = 4
    ORDER BY start_time
""", (target_date,)).fetchall()

print(f"\nFound {len(bike_activities)} bike activities\n")

for i, activity in enumerate(bike_activities, 1):
    start_time = activity[2]
    end_time = activity[3]
    title = (activity[4] or '').encode('ascii', 'ignore').decode('ascii')
    duration_ms = activity[6]
    duration_min = int(round(duration_ms / 60_000)) if duration_ms else 0
    duration_sec = int(round(duration_ms / 1000)) if duration_ms else 0
    
    start_dt = epoch_ms_to_datetime(start_time) if start_time else None
    end_dt = epoch_ms_to_datetime(end_time) if end_time else None
    
    print(f"{'='*80}")
    print(f"BIKE ACTIVITY {i}: {title}")
    print(f"{'='*80}")
    print(f"Exercise Start: {start_dt.strftime('%H:%M:%S') if start_dt else 'N/A'}")
    print(f"Exercise End: {end_dt.strftime('%H:%M:%S') if end_dt else 'N/A'}")
    print(f"Exercise Duration: {duration_min} min {duration_sec % 60} sec ({duration_sec} seconds)")
    
    # Get the FIRST calorie record that starts at or after exercise start
    first_record = conn.execute("""
        SELECT start_time, end_time, energy, local_date
        FROM total_calories_burned_record_table
        WHERE start_time >= ? AND start_time <= ?
        ORDER BY start_time
        LIMIT 1
    """, (start_time, start_time + 60000)).fetchone()  # Within 1 minute of start
    
    if first_record:
        rec_start = first_record[0]
        rec_end = first_record[1]
        rec_energy = first_record[2]
        rec_kcal = int(round(rec_energy / 1000.0)) if rec_energy else 0
        rec_duration = int((rec_end - rec_start) / 1000) if rec_end and rec_start else 0
        
        rec_start_dt = epoch_ms_to_datetime(rec_start) if rec_start else None
        
        print(f"\nFirst calorie record:")
        print(f"  Start: {rec_start_dt.strftime('%H:%M:%S') if rec_start_dt else 'N/A'}")
        print(f"  Duration: {rec_duration} seconds ({rec_duration // 60} min {rec_duration % 60} sec)")
        print(f"  Calories: {rec_kcal} kcal")
        
        # Check alignment
        time_diff = abs(rec_start - start_time) / 1000  # seconds
        print(f"\n  Time alignment: {time_diff:.0f} seconds difference from exercise start")
        
        # Check if duration matches
        duration_diff = abs(rec_duration - duration_sec)
        print(f"  Duration match: {duration_diff} seconds difference")
        
        if time_diff < 60 and duration_diff < 60:
            print(f"  -> MATCH! This record represents the workout calories")
        else:
            print(f"  -> May not be exact match, but likely represents workout")
        
        # Compare with what oHealth says
        if i == 1:
            print(f"\n  oHealth says: 119 kcal for 20:47")
            print(f"  First record: {rec_kcal} kcal for {rec_duration // 60}:{rec_duration % 60:02d}")
        elif i == 2:
            print(f"\n  oHealth says: 97 kcal for 17:45")
            print(f"  First record: {rec_kcal} kcal for {rec_duration // 60}:{rec_duration % 60:02d}")
    else:
        print("\nNo matching first record found")
    
    # Also check all records during the period for comparison
    all_records = conn.execute("""
        SELECT start_time, end_time, energy
        FROM total_calories_burned_record_table
        WHERE start_time >= ? AND end_time <= ?
        ORDER BY start_time
    """, (start_time, end_time)).fetchall()
    
    if all_records:
        total_sum = sum(r[2] for r in all_records if r[2])
        total_kcal = int(round(total_sum / 1000.0))
        print(f"\n  Sum of ALL records during activity: {total_kcal} kcal ({len(all_records)} records)")
        print(f"  First record only: {rec_kcal} kcal")
        print(f"  Difference: {total_kcal - rec_kcal} kcal")
    
    print()

# Check other exercise types too
print("=" * 80)
print("CHECKING OTHER EXERCISE TYPES")
print("=" * 80)

other_exercises = conn.execute("""
    SELECT row_id, exercise_type, start_time, end_time, title,
           (end_time - start_time) as duration_ms
    FROM exercise_session_record_table
    WHERE local_date = ? AND exercise_type != 4
    ORDER BY start_time
    LIMIT 3
""", (target_date,)).fetchall()

if other_exercises:
    print(f"\nFound {len(other_exercises)} other exercises (showing first 3):\n")
    
    for i, exercise in enumerate(other_exercises, 1):
        ex_type = exercise[1]
        start_time = exercise[2]
        end_time = exercise[3]
        title = (exercise[4] or '').encode('ascii', 'ignore').decode('ascii')
        duration_ms = exercise[5]
        duration_sec = int(round(duration_ms / 1000)) if duration_ms else 0
        
        start_dt = epoch_ms_to_datetime(start_time) if start_time else None
        
        print(f"{i}. Type {ex_type} - {title}")
        print(f"   Start: {start_dt.strftime('%H:%M:%S') if start_dt else 'N/A'}")
        print(f"   Duration: {duration_sec} seconds")
        
        # Get first record
        first_record = conn.execute("""
            SELECT start_time, end_time, energy
            FROM total_calories_burned_record_table
            WHERE start_time >= ? AND start_time <= ?
            ORDER BY start_time
            LIMIT 1
        """, (start_time, start_time + 60000)).fetchone()
        
        if first_record:
            rec_energy = first_record[2]
            rec_kcal = int(round(rec_energy / 1000.0)) if rec_energy else 0
            rec_duration = int((first_record[1] - first_record[0]) / 1000) if first_record[1] and first_record[0] else 0
            print(f"   First record: {rec_kcal} kcal, {rec_duration} seconds")
        print()

conn.close()
print("=" * 80)
