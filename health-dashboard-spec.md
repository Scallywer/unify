# Health & Fitness Dashboard — Project Specification

## Project Goal

Build a self-hosted personal health dashboard that aggregates weight, calories, steps, sleep, and workout data into a single unified web interface. All data originates from Android health apps and is funneled through Android's Health Connect API to a self-hosted backend.

---

## Data Sources & Flow

```
Mi Band ──── Mi Fitness app ──┐
Eufy Life (scale) ────────────┼──→ Android Health Connect ──[Tasker]──→ Backend API
MyFitnessPal ─────────────────┘                                              │
                                                                        Web Dashboard
```

### Source apps (user manages these, no dev work needed)
- **Xiaomi Mi Fitness** — syncs Mi Band data (steps, HR, sleep, workouts) to Health Connect
- **Eufy Life** — syncs scale weight measurements to Health Connect
- **MyFitnessPal** — syncs calorie intake to Health Connect

### Android bridge (user manages this, no dev work needed)
- **Tasker** (Android automation app) reads from Health Connect on a schedule via Health Connect plugins and HTTP POSTs the data to the backend API
- No Android app development required

---

## Metrics to Track

| Metric | Source | Unit |
|---|---|---|
| Weight | Eufy scale → Health Connect | kg |
| Calories in | MyFitnessPal → Health Connect | kcal |
| Steps | Mi Band → Health Connect | count |
| Sleep duration | Mi Band → Health Connect | hours |
| Heart rate (resting) | Mi Band → Health Connect | bpm |
| Workouts | Mi Band → Health Connect | type + duration |

---

## Backend

### Stack
- **Language:** Python 3.11+
- **Framework:** FastAPI
- **Database:** SQLite (single file, simple, no dependencies)
- **Deployment:** Docker container or bare Python, intended for self-hosted Proxmox LXC

### API Endpoints

#### Ingest (called by Tasker)
```
POST /api/ingest
Content-Type: application/json

{
  "timestamp": "2024-03-01T08:00:00",
  "weight_kg": 85.4,           // optional
  "calories_kcal": 2100,       // optional
  "steps": 8500,               // optional
  "sleep_hours": 7.2,          // optional
  "resting_hr_bpm": 62,        // optional
  "workout_type": "strength",  // optional
  "workout_duration_min": 45   // optional
}
```
- All fields except `timestamp` are optional — Tasker may POST partial payloads
- Returns `200 OK` with `{"status": "ok"}`
- Simple shared secret auth via header: `X-API-Key: <secret>`

#### Read (called by dashboard)
```
GET /api/data?days=30         // last N days of all metrics
GET /api/data/today           // today's summary
GET /api/data/weight?days=90  // single metric history
```

### Database Schema

```sql
CREATE TABLE metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  date TEXT NOT NULL,           -- YYYY-MM-DD, for daily aggregation
  weight_kg REAL,
  calories_kcal INTEGER,
  steps INTEGER,
  sleep_hours REAL,
  resting_hr_bpm INTEGER,
  workout_type TEXT,
  workout_duration_min INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_date ON metrics(date);
```

### Configuration
- Single `.env` file:
```
API_KEY=your_secret_key_here
PORT=8000
DB_PATH=./data/health.db
```

---

## Web Dashboard

### Stack
- **Single HTML file** — no build step, no framework, no bundler
- Vanilla JS + CSS only
- Charts via **Chart.js** (CDN)
- Fetches data from the backend API on load

### Layout

```
┌─────────────────────────────────────────────┐
│  🏃 Health Dashboard          [Last 7d ▼]   │
├──────────┬──────────┬──────────┬────────────┤
│ Weight   │ Calories │  Steps   │   Sleep    │
│ 85.4 kg  │ 2,100    │  8,500   │  7.2 hrs   │
│ ▼ -0.3   │ ✓ goal   │ ✓ 10k   │  ✓ 7h+     │
├──────────┴──────────┴──────────┴────────────┤
│  Weight trend (line chart, last 30 days)    │
├─────────────────────────────────────────────┤
│  Calories vs. Steps (dual axis, bar+line)   │
├─────────────────────────────────────────────┤
│  Recent workouts (list, last 7 days)        │
└─────────────────────────────────────────────┘
```

### Features
- Date range selector: 7d / 14d / 30d / 90d
- Today's summary cards at the top (weight, kcal, steps, sleep)
- Weight trend line chart
- Calories + steps combined chart (bar + line, dual Y axis)
- Workout log list (date, type, duration)
- Simple goal indicators (configurable hardcoded targets in JS):
  - Steps goal: 10,000/day
  - Sleep goal: 7h/night
  - Calories goal: configurable
- Mobile-friendly responsive layout
- Dark mode preferred

### Auth
- Dashboard served from same FastAPI backend as static file
- Protected by the same `X-API-Key` header or a simple login page with a hardcoded password (basic is fine, this is self-hosted LAN use)

---

## Project Structure

```
health-dashboard/
├── backend/
│   ├── main.py          # FastAPI app, all routes
│   ├── database.py      # SQLite connection + queries
│   ├── models.py        # Pydantic models for ingest payload
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── index.html       # Single file dashboard (HTML + CSS + JS)
├── data/                # SQLite DB lives here (gitignored)
├── docker-compose.yml   # Optional
└── README.md            # Setup instructions
```

---

## README Should Cover

1. Requirements (Python 3.11+)
2. Install & run instructions
3. How to configure Tasker to read Health Connect and POST to `/api/ingest` (example payload + headers)
4. How to set the API key in `.env`
5. How to access the dashboard

---

## Out of Scope

- No user accounts / multi-user support
- No mobile app development
- No direct integration with Mi Fitness, Eufy, or MFP APIs (Health Connect is the bridge)
- No Google Fit — not used in any part of the pipeline
- No cloud hosting — strictly self-hosted
- No notifications or alerts (v1)
