import sqlite3
import os
from datetime import date, timedelta, datetime, timezone

def epoch_days_to_date(days: int) -> str:
    return (date(1970, 1, 1) + timedelta(days=days)).isoformat()

def epoch_ms_to_datetime(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

db_path = 'import/health_connect_export_Nata.db'
if not os.path.exists(db_path):
    print(f"File not found: {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)

# Get today's date (or most recent date in database)
today = date.today()
today_epoch = (today - date(1970, 1, 1)).days

# Check what dates we have
max_date = conn.execute("SELECT MAX(local_date) FROM exercise_session_record_table").fetchone()[0]
if max_date:
    max_date_str = epoch_days_to_date(max_date)
    print(f"Most recent date in database: {max_date_str}")
    print(f"Today's date: {today.isoformat()}")
    # Use most recent date if today isn't available
    if max_date > today_epoch:
        target_date = max_date
        target_date_str = max_date_str
    else:
        target_date = today_epoch
        target_date_str = today.isoformat()
else:
    print("No exercise data found")
    conn.close()
    exit(1)

print("=" * 80)
print(f"ANALYZING BIKE ACTIVITIES FOR {target_date_str}")
print("=" * 80)

# Get all bike activities (exercise_type = 4) for target date
bike_activities = conn.execute("""
    SELECT row_id, exercise_type, start_time, end_time, title, notes,
           (end_time - start_time) as duration_ms
    FROM exercise_session_record_table
    WHERE local_date = ? AND exercise_type = 4
    ORDER BY start_time
""", (target_date,)).fetchall()

print(f"\nFound {len(bike_activities)} bike activities for {target_date_str}\n")

if not bike_activities:
    print("No bike activities found for this date")
    conn.close()
    exit(0)

# For each bike activity, get calories during that time period
for i, activity in enumerate(bike_activities, 1):
    activity_id = activity[0]
    ex_type = activity[1]
    start_time = activity[2]
    end_time = activity[3]
    title = (activity[4] or '').encode('ascii', 'ignore').decode('ascii')
    notes = (activity[5] or '').encode('ascii', 'ignore').decode('ascii')
    duration_ms = activity[6]
    
    duration_min = int(round(duration_ms / 60_000)) if duration_ms else 0
    duration_hours = duration_min / 60.0
    
    start_dt = epoch_ms_to_datetime(start_time) if start_time else None
    end_dt = epoch_ms_to_datetime(end_time) if end_time else None
    
    print(f"{'='*80}")
    print(f"BIKE ACTIVITY {i}")
    print(f"{'='*80}")
    print(f"Title: {title}")
    print(f"Notes: {notes}")
    print(f"Start: {start_dt.strftime('%Y-%m-%d %H:%M:%S') if start_dt else 'N/A'}")
    print(f"End: {end_dt.strftime('%Y-%m-%d %H:%M:%S') if end_dt else 'N/A'}")
    print(f"Duration: {duration_min} min ({duration_hours:.2f} hours)")
    print(f"Time range (epoch ms): {start_time} to {end_time}")
    
    # Get all calorie records that fall within this activity's time window
    calorie_records = conn.execute("""
        SELECT start_time, end_time, energy, local_date
        FROM total_calories_burned_record_table
        WHERE start_time >= ? AND end_time <= ?
        ORDER BY start_time
    """, (start_time, end_time)).fetchall()
    
    print(f"\nCalorie records during activity: {len(calorie_records)}")
    
    if calorie_records:
        total_energy = sum(r[2] for r in calorie_records if r[2])
        total_kcal = int(round(total_energy / 1000.0))
        kcal_per_min = total_kcal / duration_min if duration_min > 0 else 0
        kcal_per_hour = total_kcal / duration_hours if duration_hours > 0 else 0
        
        print(f"Total calories during activity: {total_kcal} kcal")
        print(f"Rate: {kcal_per_min:.2f} kcal/min = {kcal_per_hour:.0f} kcal/hour")
        
        # Show first few and last few records
        print(f"\nFirst 5 calorie records:")
        for j, record in enumerate(calorie_records[:5], 1):
            rec_start = epoch_ms_to_datetime(record[0]) if record[0] else None
            rec_energy = int(round(record[2] / 1000.0)) if record[2] else 0
            rec_duration = int((record[1] - record[0]) / 1000) if record[1] and record[0] else 0
            print(f"  {j}. {rec_start.strftime('%H:%M:%S') if rec_start else 'N/A':8s} - {rec_energy:3d} kcal ({rec_duration:2d}s)")
        
        if len(calorie_records) > 10:
            print(f"\n... ({len(calorie_records) - 10} more records) ...")
            print(f"\nLast 5 calorie records:")
            for j, record in enumerate(calorie_records[-5:], len(calorie_records) - 4):
                rec_start = epoch_ms_to_datetime(record[0]) if record[0] else None
                rec_energy = int(round(record[2] / 1000.0)) if record[2] else 0
                rec_duration = int((record[1] - record[0]) / 1000) if record[1] and record[0] else 0
                print(f"  {j}. {rec_start.strftime('%H:%M:%S') if rec_start else 'N/A':8s} - {rec_energy:3d} kcal ({rec_duration:2d}s)")
        
        # Check for gaps
        gaps = []
        for j in range(len(calorie_records) - 1):
            if calorie_records[j][1] and calorie_records[j+1][0]:
                gap = calorie_records[j+1][0] - calorie_records[j][1]
                if gap > 60000:  # More than 1 minute
                    gaps.append((j, gap / 1000))
        
        if gaps:
            print(f"\nGaps in calorie records: {len(gaps)} gaps > 1 minute")
            for gap_idx, gap_sec in gaps[:3]:
                print(f"  Gap after record {gap_idx+1}: {gap_sec:.0f} seconds")
    else:
        print("No calorie records found during this activity time period")
        print("\nChecking for overlapping records (not exact match)...")
        overlapping = conn.execute("""
            SELECT SUM(energy) as total_energy, COUNT(*) as cnt
            FROM total_calories_burned_record_table
            WHERE (start_time <= ? AND end_time >= ?) OR (start_time <= ? AND end_time >= ?)
        """, (start_time, start_time, end_time, end_time)).fetchone()
        
        if overlapping and overlapping[0]:
            total_kcal = int(round(overlapping[0] / 1000.0))
            print(f"Found {overlapping[1]} overlapping records with {total_kcal} kcal total")
    
    print()

conn.close()
print("=" * 80)
