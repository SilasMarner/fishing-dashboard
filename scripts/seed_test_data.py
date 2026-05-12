#!/usr/bin/env python3
"""
Seed the fish_log database with one week of realistic shark catches
for testing the AI analysis feature.

Usage (from repo root):
    python3 scripts/seed_test_data.py

The script auto-detects the DB path from the DB_PATH env var,
falling back to ./data/fish_log.db — the same path used by the
Docker container via the ./data volume mount.

Safe to run multiple times: each run inserts a fresh week of data
and deletes any stale AI analysis entries so the next Run Now
generates a clean Groq report.
"""

import os
import random
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

DB = os.environ.get("DB_PATH", "./data/fish_log.db")
TZ = ZoneInfo("America/Chicago")

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS fish_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    location      TEXT NOT NULL,
    species       TEXT NOT NULL,
    caught        INTEGER NOT NULL DEFAULT 1,
    fish_count    INTEGER DEFAULT 1,
    size_in       REAL,
    weight_lbs    REAL,
    notes         TEXT,
    tide_height_ft  REAL,
    tide_stage      TEXT,
    water_level_ft  REAL,
    pressure_mb     REAL,
    pressure_trend  REAL,
    temp_f          REAL,
    wind_speed_mph  REAL,
    wind_deg        REAL,
    precip_chance   INTEGER,
    humidity        INTEGER,
    cloud_cover     INTEGER,
    solunar_period  TEXT,
    moon_phase_pct  REAL,
    fishing_score   REAL
);
CREATE TABLE IF NOT EXISTS ai_analysis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    location      TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    content       TEXT NOT NULL,
    model         TEXT
);
"""

# ── Shark species: (min_in, max_in, min_lb, max_lb) ───────────────────────────
SHARKS = {
    "Atlantic Sharpnose Shark":    (24,  40,   4,  14),
    "Bonnethead Shark":            (24,  38,   4,  12),
    "Blacktip Shark":              (36,  65,  14,  45),
    "Spinner Shark":               (45,  72,  25,  70),
    "Finetooth Shark":             (30,  50,   8,  22),
    "Bull Shark":                  (60,  96,  80, 280),
    "Tiger Shark":                 (84, 144, 300, 850),
    "Great Hammerhead Shark":      (90, 150, 400, 950),
    "Scalloped Hammerhead Shark":  (60, 108, 100, 340),
    "Lemon Shark":                 (60,  96,  60, 200),
    "Nurse Shark":                 (48,  84,  40, 150),
}

LOCATIONS  = ["freeport_tx", "padre_island_tx", "pensacola_fl", "sargent_tx", "matagorda_tx"]
STAGES     = ["incoming", "outgoing", "high slack", "low slack"]
SOLUNAR    = ["major", "major", "minor", "minor", "none"]

NOTES_CAUGHT = [
    "Caught on cut mullet off the jetty",
    "Hit a chunk of stingray on the bottom",
    "Fought hard for 20 min before landing",
    "Released after quick measurement",
    "Caught on whole whiting fished on the bottom",
    "Hit cut ladyfish near the channel edge",
    "Came up on a 10/0 circle hook",
    "Caught in the surf on a fish finder rig",
    "Early morning bite — took about 15 min to land",
    "Tagged and released — nice specimen",
]
NOTES_MISSED = [
    "Dropped the bait before hook-up",
    "Leader cut — likely shark bite-off",
    "Long run then threw the hook",
    "Hit and ran with the bait, never set",
    "Circled the bait but wouldn't commit",
    "Bit through 80lb mono",
]

# Conditions per day (pressure_mb, trend, temp_f, wind_mph, wind_deg, precip, humidity, cloud)
DAY_CONDITIONS = [
    (1018.2,  1, 78, 10, 135, 10, 72, 25),  # Mon — rising, calm, clear
    (1016.5,  0, 80, 14, 160, 15, 76, 40),  # Tue — steady, SE breeze
    (1013.8, -1, 82, 18, 175, 30, 82, 60),  # Wed — falling, building
    (1009.4, -1, 79, 22, 190, 50, 88, 80),  # Thu — low, windy, overcast
    (1011.2,  1, 77, 16, 200, 25, 80, 55),  # Fri — recovering
    (1014.7,  1, 79, 12, 220, 10, 74, 30),  # Sat — rising, improving
    (1017.1,  1, 81, 10, 180,  5, 70, 20),  # Sun — high pressure, calm
]

# (day_offset, location, species, caught, hour, minute)
ENTRIES_PLAN = [
    # Monday
    (0, "freeport_tx",    "Atlantic Sharpnose Shark",   True,  6, 20),
    (0, "freeport_tx",    "Blacktip Shark",             True,  7, 45),
    (0, "freeport_tx",    "Atlantic Sharpnose Shark",   True, 19, 10),
    (0, "padre_island_tx","Bonnethead Shark",           True,  7,  5),
    (0, "padre_island_tx","Blacktip Shark",             False, 8, 30),
    (0, "sargent_tx",     "Atlantic Sharpnose Shark",   True,  6, 50),
    (0, "matagorda_tx",   "Bonnethead Shark",           True,  7, 15),
    (0, "pensacola_fl",   "Blacktip Shark",             True,  7, 30),
    # Tuesday
    (1, "freeport_tx",    "Spinner Shark",              True,  8,  0),
    (1, "freeport_tx",    "Atlantic Sharpnose Shark",   True, 18, 45),
    (1, "padre_island_tx","Atlantic Sharpnose Shark",   True,  6, 10),
    (1, "padre_island_tx","Bull Shark",                 True, 20, 30),
    (1, "sargent_tx",     "Bonnethead Shark",           True,  7, 20),
    (1, "sargent_tx",     "Blacktip Shark",             False, 9, 15),
    (1, "matagorda_tx",   "Atlantic Sharpnose Shark",   True,  6, 55),
    (1, "pensacola_fl",   "Nurse Shark",                True, 10, 20),
    # Wednesday
    (2, "freeport_tx",    "Blacktip Shark",             True,  7,  5),
    (2, "freeport_tx",    "Bull Shark",                 False,14, 30),
    (2, "padre_island_tx","Finetooth Shark",            True,  6, 40),
    (2, "padre_island_tx","Blacktip Shark",             True,  7, 55),
    (2, "sargent_tx",     "Atlantic Sharpnose Shark",   False, 8, 10),
    (2, "matagorda_tx",   "Spinner Shark",              True, 19, 20),
    (2, "pensacola_fl",   "Scalloped Hammerhead Shark", True, 11,  0),
    (2, "pensacola_fl",   "Blacktip Shark",             False,16, 45),
    # Thursday
    (3, "freeport_tx",    "Atlantic Sharpnose Shark",   True,  7, 30),
    (3, "freeport_tx",    "Bonnethead Shark",           False,12,  0),
    (3, "padre_island_tx","Atlantic Sharpnose Shark",   False, 8, 20),
    (3, "sargent_tx",     "Blacktip Shark",             False,10, 30),
    (3, "matagorda_tx",   "Atlantic Sharpnose Shark",   False, 9,  0),
    (3, "pensacola_fl",   "Lemon Shark",                True, 18, 10),
    # Friday
    (4, "freeport_tx",    "Blacktip Shark",             True,  6, 15),
    (4, "freeport_tx",    "Spinner Shark",              True, 19, 30),
    (4, "freeport_tx",    "Bull Shark",                 True, 20, 45),
    (4, "padre_island_tx","Blacktip Shark",             True,  6, 50),
    (4, "padre_island_tx","Atlantic Sharpnose Shark",   True,  7, 25),
    (4, "sargent_tx",     "Bonnethead Shark",           True,  6, 30),
    (4, "matagorda_tx",   "Blacktip Shark",             True,  7,  0),
    (4, "pensacola_fl",   "Tiger Shark",                True, 21, 15),
    (4, "pensacola_fl",   "Blacktip Shark",             True,  7, 40),
    # Saturday
    (5, "freeport_tx",    "Atlantic Sharpnose Shark",   True,  5, 50),
    (5, "freeport_tx",    "Blacktip Shark",             True,  6, 30),
    (5, "freeport_tx",    "Bull Shark",                 True,  7, 45),
    (5, "freeport_tx",    "Spinner Shark",              True, 19,  0),
    (5, "padre_island_tx","Blacktip Shark",             True,  6,  0),
    (5, "padre_island_tx","Great Hammerhead Shark",     True, 22, 30),
    (5, "padre_island_tx","Atlantic Sharpnose Shark",   True,  7, 10),
    (5, "sargent_tx",     "Blacktip Shark",             True,  6, 20),
    (5, "sargent_tx",     "Bull Shark",                 True, 20, 15),
    (5, "matagorda_tx",   "Atlantic Sharpnose Shark",   True,  6, 45),
    (5, "matagorda_tx",   "Blacktip Shark",             True,  7, 30),
    (5, "pensacola_fl",   "Scalloped Hammerhead Shark", True,  9, 20),
    (5, "pensacola_fl",   "Spinner Shark",              True, 18, 50),
    # Sunday
    (6, "freeport_tx",    "Atlantic Sharpnose Shark",   True,  6,  5),
    (6, "freeport_tx",    "Blacktip Shark",             True,  7, 20),
    (6, "freeport_tx",    "Finetooth Shark",            True,  8, 10),
    (6, "padre_island_tx","Bonnethead Shark",           True,  6, 30),
    (6, "padre_island_tx","Blacktip Shark",             True,  7, 50),
    (6, "padre_island_tx","Lemon Shark",                True, 20,  0),
    (6, "sargent_tx",     "Atlantic Sharpnose Shark",   True,  6, 15),
    (6, "sargent_tx",     "Bonnethead Shark",           True,  7, 40),
    (6, "matagorda_tx",   "Atlantic Sharpnose Shark",   True,  5, 55),
    (6, "matagorda_tx",   "Spinner Shark",              True, 19, 10),
    (6, "pensacola_fl",   "Nurse Shark",                True,  9, 30),
    (6, "pensacola_fl",   "Bull Shark",                 True, 21,  0),
]


def main():
    import os
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)

    with sqlite3.connect(DB) as conn:
        conn.executescript(SCHEMA)
        conn.commit()

    random.seed(42)

    # Seed entries starting 7 days before today
    from datetime import timedelta
    today = datetime.now(tz=TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    base_day = today - timedelta(days=7)

    rows = []
    for (day, loc, species, caught, hour, minute) in ENTRIES_PLAN:
        mb, trend, temp, wind_mph, wind_deg, precip, humidity, cloud = DAY_CONDITIONS[day]
        mb       = round(mb + random.uniform(-0.3, 0.3), 1)
        temp     = round(temp + random.uniform(-1, 1), 1)
        wind_mph = round(wind_mph + random.uniform(-2, 2), 1)
        tide_ft  = round(random.uniform(-0.4, 2.4), 2)
        stage    = random.choice(STAGES)
        solunar  = SOLUNAR[day % len(SOLUNAR)]
        moon_pct = round(45 + day * 3.5 + random.uniform(-2, 2), 1)
        score    = round(random.uniform(5.5, 9.2) if caught else random.uniform(2.0, 5.5), 1)

        if caught:
            min_in, max_in, min_lb, max_lb = SHARKS[species]
            size   = round(random.uniform(min_in, max_in), 1)
            weight = round(random.uniform(min_lb, max_lb), 1)
            notes  = random.choice(NOTES_CAUGHT)
            count  = 1
        else:
            size = weight = None
            notes = random.choice(NOTES_MISSED)
            count = 0

        dt = base_day.replace(day=base_day.day + day, hour=hour, minute=minute)
        ts = int(dt.timestamp())
        rows.append((
            ts, loc, species, int(caught), count, size, weight, notes,
            tide_ft, stage, None, mb, float(trend), temp,
            wind_mph, float(wind_deg), precip, humidity, cloud,
            solunar, moon_pct, score,
        ))

    with sqlite3.connect(DB) as conn:
        conn.executemany("""
            INSERT INTO fish_log
              (logged_at, location, species, caught, fish_count, size_in, weight_lbs, notes,
               tide_height_ft, tide_stage, water_level_ft, pressure_mb, pressure_trend, temp_f,
               wind_speed_mph, wind_deg, precip_chance, humidity, cloud_cover,
               solunar_period, moon_phase_pct, fishing_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

        # Remove stale/error AI analysis entries so the next Run Now starts clean
        deleted = conn.execute(
            "DELETE FROM ai_analysis WHERE content LIKE '%unavailable%' OR content LIKE '%not set%'"
        ).rowcount
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM fish_log").fetchone()[0]

    print(f"Inserted {len(rows)} test entries  |  fish_log total: {total} rows")
    if deleted:
        print(f"Removed {deleted} stale AI analysis error entries")
    print(f"\nDatabase: {os.path.abspath(DB)}")
    print("\nNext step: open the Analysis tab and click 'Run Now' for each location.")


if __name__ == "__main__":
    main()
