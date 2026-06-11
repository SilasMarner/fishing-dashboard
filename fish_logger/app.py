#!/usr/bin/env python3
"""
Fish Logger — catch logging + AI analysis correlated with tide/weather/solunar data.
"""
import base64
import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# import anthropic          # ── Anthropic/Claude (commented out; see run_ai_analysis below)
# from google import genai  # ── Gemini (commented out; see run_ai_analysis below)
from openai import OpenAI
import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fish_logger")

app = Flask(__name__)

DB_PATH              = os.environ.get("DB_PATH", "/data/fish_log.db")
PROMETHEUS_URL       = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
EXPORTER_QUERY_URL   = os.environ.get("EXPORTER_QUERY_URL", "http://localhost:9878")
GROQ_KEY             = os.environ.get("GROQ_API_KEY", "")
XAI_KEY              = os.environ.get("XAI_API_KEY", "")
AI_PROVIDER          = os.environ.get("AI_PROVIDER", "groq").lower()  # "groq" or "xai"
ANALYSIS_HOURS       = int(os.environ.get("ANALYSIS_INTERVAL_HOURS", "6"))
# Analysis runs ON-DEMAND only (the /analysis page button → /api/analyze). The old
# background scheduler re-ran every location every ANALYSIS_HOURS, which quietly burned
# the daily LLM token budget; set ANALYSIS_AUTO=true to bring that back if ever wanted.
ANALYSIS_AUTO        = os.environ.get("ANALYSIS_AUTO", "false").lower() in ("1", "true", "yes", "on")
# Model used for the analysis report; override to dodge per-model rate limits.
ANALYSIS_MODEL       = os.environ.get("ANALYSIS_MODEL", "") or None
PORT                 = int(os.environ.get("PORT", "9879"))
APP_TZ               = ZoneInfo("America/Chicago")
# GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "")      # ── uncomment to use Gemini instead

# ── Handwritten-log import ─────────────────────────────────────────────────────
# Two routes to structured entries:
#   • OCR methods (tesseract / ocr_space) read the page to raw text — FREE — then the
#     existing text provider (Groq/xAI, AI_PROVIDER) structures that text into entries.
#   • anthropic: Claude vision does OCR + structuring in one shot (best for handwriting,
#     uses Anthropic credits).
ANTHROPIC_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
IMPORT_MODEL         = os.environ.get("IMPORT_MODEL", "claude-opus-4-8")
IMPORT_MAX_IMAGES    = int(os.environ.get("IMPORT_MAX_IMAGES", "20"))
ALLOWED_IMG_TYPES    = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# OCR method selection (default = the free, on-device option)
OCR_PROVIDER_DEFAULT = os.environ.get("OCR_PROVIDER", "ocr_space").lower()
OCR_SPACE_API_KEY    = os.environ.get("OCR_SPACE_API_KEY", "helloworld")  # free demo key
OCR_SPACE_URL        = "https://api.ocr.space/parse/image"
# label shown in the /import dropdown
OCR_METHODS = {
    "tesseract": "Tesseract — free, on-device",
    "ocr_space": "OCR.space — free, online",
    "anthropic": "Claude vision — best for handwriting (uses credits)",
}

# ── Species by location ────────────────────────────────────────────────────────
SPECIES = {
    "freeport_tx": [
        # Inshore / bay
        "Spotted Seatrout", "Red Drum (Redfish)", "Southern Flounder", "Black Drum",
        "Sheepshead", "Sand Trout", "Silver Seatrout", "Atlantic Croaker",
        "Southern Kingfish (Whiting)", "Gulf Kingfish (Whiting)", "Pigfish",
        "Pinfish", "Striped Mullet", "Ladyfish",
        "Gafftopsail Catfish", "Hardhead Catfish",
        "Alligator Gar", "Longnose Gar",
        # Nearshore Gulf / surf
        "Tarpon", "Cobia", "Pompano", "Florida Pompano", "Permit", "Lookdown",
        "Jack Crevalle", "Tripletail", "Bluefish",
        "Spanish Mackerel", "King Mackerel", "Little Tunny", "Mahi-Mahi",
        "Greater Amberjack", "African Pompano",
        "Red Snapper", "Gray Snapper (Mangrove)", "Lane Snapper",
        "Atlantic Spadefish",
        # Sharks
        "Atlantic Sharpnose Shark", "Bonnethead Shark", "Blacktip Shark",
        "Spinner Shark", "Finetooth Shark", "Bull Shark", "Tiger Shark",
        "Great Hammerhead Shark", "Scalloped Hammerhead Shark",
        "Lemon Shark", "Nurse Shark",
        "Other",
    ],
    "padre_island_tx": [
        # Inshore / bay
        "Spotted Seatrout", "Red Drum (Redfish)", "Southern Flounder", "Black Drum",
        "Sheepshead", "Sand Trout", "Silver Seatrout", "Atlantic Croaker",
        "Southern Kingfish (Whiting)", "Gulf Kingfish (Whiting)", "Pigfish",
        "Pinfish", "Striped Mullet", "Ladyfish", "Snook",
        "Gafftopsail Catfish", "Hardhead Catfish",
        # Nearshore Gulf / surf
        "Tarpon", "Cobia", "Pompano", "Florida Pompano", "Permit", "Lookdown",
        "Jack Crevalle", "Tripletail", "Bluefish",
        "Spanish Mackerel", "King Mackerel", "Little Tunny", "Mahi-Mahi",
        "Greater Amberjack", "African Pompano",
        "Red Snapper", "Gray Snapper (Mangrove)", "Lane Snapper", "Vermilion Snapper",
        "Atlantic Spadefish",
        # Sharks
        "Atlantic Sharpnose Shark", "Bonnethead Shark", "Blacktip Shark",
        "Spinner Shark", "Finetooth Shark", "Bull Shark", "Tiger Shark",
        "Great Hammerhead Shark", "Scalloped Hammerhead Shark",
        "Lemon Shark", "Nurse Shark",
        "Other",
    ],
    "pensacola_fl": [
        # Inshore / bay
        "Spotted Seatrout", "Red Drum (Redfish)", "Southern Flounder", "Black Drum",
        "Sheepshead", "Sand Trout", "Silver Seatrout", "Atlantic Croaker",
        "Southern Kingfish (Whiting)", "Gulf Kingfish (Whiting)", "Pigfish",
        "Pinfish", "Striped Mullet", "Ladyfish", "Snook",
        # Nearshore Gulf
        "Tarpon", "Cobia", "Pompano", "Florida Pompano", "Permit",
        "African Pompano", "Lookdown", "Palometa",
        "Jack Crevalle", "Tripletail", "Bluefish", "Little Tunny", "Mahi-Mahi",
        "Spanish Mackerel", "King Mackerel", "Wahoo",
        "Greater Amberjack", "Lesser Amberjack", "Banded Rudderfish",
        "Gray Triggerfish", "Atlantic Spadefish",
        # Snapper / grouper
        "Red Snapper", "Vermilion Snapper", "Lane Snapper",
        "Gray Snapper (Mangrove)", "Cubera Snapper",
        "Gag Grouper", "Red Grouper", "Scamp Grouper",
        "White Grunt", "Hogfish", "Black Sea Bass",
        # Sharks
        "Atlantic Sharpnose Shark", "Bonnethead Shark", "Blacktip Shark",
        "Spinner Shark", "Finetooth Shark", "Bull Shark", "Tiger Shark",
        "Great Hammerhead Shark", "Scalloped Hammerhead Shark",
        "Lemon Shark", "Nurse Shark",
        "Other",
    ],
    "sargent_tx": [
        # Inshore / bay
        "Spotted Seatrout", "Red Drum (Redfish)", "Southern Flounder", "Black Drum",
        "Sheepshead", "Sand Trout", "Silver Seatrout", "Atlantic Croaker",
        "Southern Kingfish (Whiting)", "Gulf Kingfish (Whiting)", "Pigfish",
        "Pinfish", "Striped Mullet", "Ladyfish",
        "Gafftopsail Catfish", "Hardhead Catfish",
        "Alligator Gar", "Longnose Gar",
        # Nearshore Gulf / surf
        "Tarpon", "Cobia", "Pompano", "Florida Pompano", "Permit", "Lookdown",
        "Jack Crevalle", "Tripletail", "Bluefish",
        "Spanish Mackerel", "King Mackerel", "Little Tunny", "Mahi-Mahi",
        "Greater Amberjack", "African Pompano",
        "Red Snapper", "Gray Snapper (Mangrove)", "Lane Snapper",
        "Atlantic Spadefish",
        # Sharks
        "Atlantic Sharpnose Shark", "Bonnethead Shark", "Blacktip Shark",
        "Spinner Shark", "Finetooth Shark", "Bull Shark", "Tiger Shark",
        "Great Hammerhead Shark", "Scalloped Hammerhead Shark",
        "Lemon Shark", "Nurse Shark",
        "Other",
    ],
    "matagorda_tx": [
        # Inshore / bay
        "Spotted Seatrout", "Red Drum (Redfish)", "Southern Flounder", "Black Drum",
        "Sheepshead", "Sand Trout", "Silver Seatrout", "Atlantic Croaker",
        "Southern Kingfish (Whiting)", "Gulf Kingfish (Whiting)", "Pigfish",
        "Pinfish", "Striped Mullet", "Ladyfish",
        "Gafftopsail Catfish", "Hardhead Catfish",
        "Alligator Gar", "Longnose Gar",
        # Nearshore Gulf / surf
        "Tarpon", "Cobia", "Pompano", "Florida Pompano", "Permit", "Lookdown",
        "Jack Crevalle", "Tripletail", "Bluefish",
        "Spanish Mackerel", "King Mackerel", "Little Tunny", "Mahi-Mahi",
        "Greater Amberjack", "African Pompano",
        "Red Snapper", "Gray Snapper (Mangrove)", "Lane Snapper",
        "Atlantic Spadefish",
        # Sharks
        "Atlantic Sharpnose Shark", "Bonnethead Shark", "Blacktip Shark",
        "Spinner Shark", "Finetooth Shark", "Bull Shark", "Tiger Shark",
        "Great Hammerhead Shark", "Scalloped Hammerhead Shark",
        "Lemon Shark", "Nurse Shark",
        "Other",
    ],
}

LOCATION_NAMES = {
    "freeport_tx":    "Freeport TX",
    "padre_island_tx": "N Padre Island TX",
    "pensacola_fl":   "Pensacola FL",
    "sargent_tx":     "Sargent TX",
    "matagorda_tx":   "Matagorda TX",
}

# NOAA tide station IDs matching the exporter's STATIONS dict
NOAA_TIDE_STATION_IDS = {
    "freeport_tx":    "8772447",
    "padre_island_tx": "8779770",
    "pensacola_fl":   "8729840",
    "sargent_tx":     "8772985",
    "matagorda_tx":   "8773146",
}

# ── NOAA station list cache (all tide prediction stations, refreshed daily) ────
_noaa_stations: list = []
_noaa_stations_ts: float = 0.0
_noaa_lock = threading.Lock()

def _get_noaa_stations() -> list:
    global _noaa_stations, _noaa_stations_ts
    now = time.time()
    if _noaa_stations and now - _noaa_stations_ts < 86400:
        return _noaa_stations
    with _noaa_lock:
        if _noaa_stations and now - _noaa_stations_ts < 86400:
            return _noaa_stations
        r = requests.get(
            "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json",
            params={"type": "tidepredictions", "units": "english"},
            timeout=15,
        )
        r.raise_for_status()
        _noaa_stations = [
            {"id": s["id"], "name": s["name"], "state": s.get("state", ""),
             "lat": s.get("lat"), "lng": s.get("lng")}
            for s in r.json().get("stations", [])
        ]
        _noaa_stations_ts = now
        log.info("Loaded %d NOAA tide stations", len(_noaa_stations))
        return _noaa_stations

# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fish_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                location      TEXT NOT NULL,
                -- specific spot / body of water written on the log (e.g. "West
                -- Galveston Bay", "Cedar Lakes"); free-text, NOT one of the favorite
                -- stations. Sortable in the catches view and surfaced in analysis.
                caught_location TEXT,
                species       TEXT NOT NULL,
                caught        INTEGER NOT NULL DEFAULT 1,
                fish_count    INTEGER DEFAULT 1,
                size_in       REAL,
                weight_lbs    REAL,
                notes         TEXT,
                -- conditions snapshot
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
        """)
        # ── lightweight migrations for pre-existing DBs ──────────────────────────
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fish_log)")}
        if "caught_location" not in cols:
            conn.execute("ALTER TABLE fish_log ADD COLUMN caught_location TEXT")
        if "bait" not in cols:
            conn.execute("ALTER TABLE fish_log ADD COLUMN bait TEXT")
        if "tackle" not in cols:
            conn.execute("ALTER TABLE fish_log ADD COLUMN tackle TEXT")
        conn.commit()

# ── Prometheus helpers ─────────────────────────────────────────────────────────
def prom_query(metric: str) -> dict[str, float]:
    """Instant query; returns {location_label: value}."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": metric},
            timeout=2,
        )
        r.raise_for_status()
        out = {}
        for item in r.json().get("data", {}).get("result", []):
            loc = item["metric"].get("location", "?")
            try:
                out[loc] = float(item["value"][1])
            except (ValueError, IndexError):
                pass
        return out
    except Exception as exc:
        log.warning("Prometheus query failed (%s): %s", metric, exc)
        return {}

def prom_labeled(metric: str, extra_label: str) -> dict[tuple, float]:
    """Returns {(location, extra_label_value): value}."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": metric},
            timeout=2,
        )
        r.raise_for_status()
        out = {}
        for item in r.json().get("data", {}).get("result", []):
            loc  = item["metric"].get("location", "?")
            lval = item["metric"].get(extra_label, "?")
            try:
                out[(loc, lval)] = float(item["value"][1])
            except (ValueError, IndexError):
                pass
        return out
    except Exception as exc:
        log.warning("Prometheus query failed (%s): %s", metric, exc)
        return {}

def get_conditions(location: str) -> dict:
    scalars = {
        "tide_height_ft":  "fishing_tide_height_ft{event_rank='1'}",
        "water_level_ft":  "fishing_water_level_ft",
        "pressure_mb":     "fishing_weather_pressure_mb",
        "pressure_trend":  "fishing_weather_pressure_trend",
        "temp_f":          "fishing_weather_temp_f",
        "wind_speed_mph":  "fishing_weather_wind_speed_mph",
        "wind_deg":        "fishing_weather_wind_deg",
        "precip_chance":   "fishing_weather_precip_chance_pct",
        "humidity":        "fishing_weather_humidity_pct",
        "cloud_cover":     "fishing_weather_cloud_cover_pct",
        "moon_phase_pct":  "fishing_moon_phase_pct",
        "fishing_score":   "fishing_score_now",
    }
    cond = {k: prom_query(v).get(location) for k, v in scalars.items()}

    # Solunar: check all period labels for major/minor
    now = time.time()
    solunar = "none"
    major_starts = prom_labeled("fishing_solunar_major_start_unix", "period")
    major_ends   = prom_labeled("fishing_solunar_major_end_unix",   "period")
    minor_starts = prom_labeled("fishing_solunar_minor_start_unix", "period")
    minor_ends   = prom_labeled("fishing_solunar_minor_end_unix",   "period")

    for (loc, p), start in major_starts.items():
        if loc == location:
            end = major_ends.get((loc, p))
            if end and start <= now <= end:
                solunar = "major"
                break
    if solunar == "none":
        for (loc, p), start in minor_starts.items():
            if loc == location:
                end = minor_ends.get((loc, p))
                if end and start <= now <= end:
                    solunar = "minor"
                    break

    cond["solunar_period"] = solunar
    cond["tide_stage"] = None
    return cond


def get_conditions_for_date(location: str, target_dt: datetime,
                            allow_live_fallback: bool = True) -> dict:
    """Return conditions for any datetime. Uses Prometheus for today, exporter query for other dates.

    allow_live_fallback=False is for historical imports: if the exporter query
    fails we return empty conditions rather than poisoning an old entry with
    *today's* live weather (and we skip the live path entirely).
    """
    today = datetime.now(tz=APP_TZ).date()
    if target_dt.date() == today and allow_live_fallback:
        return get_conditions(location)
    try:
        r = requests.get(
            f"{EXPORTER_QUERY_URL}/query",
            params={"location": location, "date": target_dt.strftime("%Y%m%d")},
            timeout=(15 if allow_live_fallback else 6),
        )
        r.raise_for_status()
        data = r.json()
        wx   = data.get("weather", {})
        sol  = data.get("solunar", {})
        moon = data.get("moon", {})
        fish = data.get("fishing", {})
        ts   = target_dt.timestamp()
        solunar = "none"
        for key in ("major1", "major2"):
            if sol.get(f"{key}_start") and sol.get(f"{key}_end"):
                if sol[f"{key}_start"] <= ts <= sol[f"{key}_end"]:
                    solunar = "major"
                    break
        if solunar == "none":
            for key in ("minor1", "minor2"):
                if sol.get(f"{key}_start") and sol.get(f"{key}_end"):
                    if sol[f"{key}_start"] <= ts <= sol[f"{key}_end"]:
                        solunar = "minor"
                        break
        return {
            "tide_height_ft":  None,
            "tide_stage":      None,
            "water_level_ft":  data.get("water_level_ft"),
            "pressure_mb":     wx.get("pressure_mb"),
            "pressure_trend":  None,
            "temp_f":          wx.get("temp_f"),
            "wind_speed_mph":  wx.get("wind_speed_mph"),
            "wind_deg":        wx.get("wind_deg"),
            "precip_chance":   wx.get("precip_chance"),
            "humidity":        wx.get("humidity"),
            "cloud_cover":     wx.get("cloud_cover"),
            "moon_phase_pct":  moon.get("illumination"),
            "fishing_score":   fish.get("score"),
            "solunar_period":  solunar,
        }
    except Exception as exc:
        log.warning("Exporter query failed for %s %s: %s", location, target_dt.date(), exc)
        return get_conditions(location) if allow_live_fallback else {}

# ── AI analysis ────────────────────────────────────────────────────────────────
TREND_LABEL = {"-1.0": "falling", "-1": "falling", "0.0": "steady", "0": "steady",
               "1.0": "rising", "1": "rising"}

ANALYSIS_WINDOWS = {
    "all":    "All Time",
    "year":   "Past Year",
    "month":  "Past Month",
    "season": "This Season",
}

def _season_start_ts() -> int:
    now = datetime.now(tz=ZoneInfo("America/Chicago"))
    m = now.month
    if m in (3, 4, 5):
        start = datetime(now.year, 3, 1, tzinfo=ZoneInfo("America/Chicago"))
    elif m in (6, 7, 8):
        start = datetime(now.year, 6, 1, tzinfo=ZoneInfo("America/Chicago"))
    elif m in (9, 10, 11):
        start = datetime(now.year, 9, 1, tzinfo=ZoneInfo("America/Chicago"))
    else:
        year = now.year if now.month == 12 else now.year - 1
        start = datetime(year, 12, 1, tzinfo=ZoneInfo("America/Chicago"))
    return int(start.timestamp())

def _season_name() -> str:
    m = datetime.now().month
    if m in (3, 4, 5):   return "Spring"
    if m in (6, 7, 8):   return "Summer"
    if m in (9, 10, 11): return "Fall"
    return "Winter"

def run_ai_analysis(location: str, window: str = "all", model: str | None = None) -> str:
    if AI_PROVIDER == "xai" and not XAI_KEY:
        return "AI analysis unavailable — XAI_API_KEY not set."
    if AI_PROVIDER != "xai" and not GROQ_KEY:
        return "AI analysis unavailable — GROQ_API_KEY not set."

    now_ts = int(datetime.now(tz=ZoneInfo("America/Chicago")).timestamp())
    if window == "month":
        cutoff = now_ts - 30 * 86400
        sql, params = ("SELECT * FROM fish_log WHERE location=? AND logged_at>=? ORDER BY logged_at DESC LIMIT 2000",
                       (location, cutoff))
        window_desc = "the past 30 days"
    elif window == "year":
        cutoff = now_ts - 365 * 86400
        sql, params = ("SELECT * FROM fish_log WHERE location=? AND logged_at>=? ORDER BY logged_at DESC LIMIT 2000",
                       (location, cutoff))
        window_desc = "the past year"
    elif window == "season":
        cutoff = _season_start_ts()
        sql, params = ("SELECT * FROM fish_log WHERE location=? AND logged_at>=? ORDER BY logged_at DESC LIMIT 2000",
                       (location, cutoff))
        window_desc = f"this {_season_name()} season"
    else:
        sql, params = ("SELECT * FROM fish_log WHERE location=? ORDER BY logged_at DESC LIMIT 2000",
                       (location,))
        window_desc = "all time"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return f"No fishing data logged yet for {LOCATION_NAMES.get(location, location)} ({window_desc})."

    tz = ZoneInfo("America/Chicago")
    entries = []
    for r in rows:
        dt = datetime.fromtimestamp(r["logged_at"], tz=tz)
        trend_raw = str(r["pressure_trend"]) if r["pressure_trend"] is not None else None
        entries.append({
            "date":           dt.strftime("%Y-%m-%d %H:%M %a"),
            "spot":           r["caught_location"],
            "species":        r["species"],
            "caught":         bool(r["caught"]),
            "count":          r["fish_count"],
            "size_in":        r["size_in"],
            "weight_lbs":     r["weight_lbs"],
            "notes":          r["notes"],
            "bait":           r["bait"],
            "tackle":         r["tackle"],
            "pressure_mb":    r["pressure_mb"],
            "pressure_trend": TREND_LABEL.get(trend_raw, trend_raw),
            "temp_f":         r["temp_f"],
            "wind_mph":       r["wind_speed_mph"],
            "tide_ft":        r["tide_height_ft"],
            "precip_pct":     r["precip_chance"],
            "humidity_pct":   r["humidity"],
            "solunar":        r["solunar_period"],
            "moon_pct":       r["moon_phase_pct"],
            "score":          r["fishing_score"],
        })

    location_name = LOCATION_NAMES.get(location, location)
    prompt = f"""You are an expert fishing guide and data analyst for Gulf Coast fishing at {location_name}.

Analyze {len(entries)} fishing log entries ({window_desc}) and provide a thorough report with these sections:

## Conditions That Produce Catches
Which combinations of barometric pressure (and trend), tide stage/height, solunar period, temperature, and wind correlate most strongly with success vs. failure? Cite specific numbers where sample size allows (e.g. "8 of 10 catches occurred when pressure was rising above 1015 mb").

## Species Breakdown
For each species with enough data, note the conditions that produced catches, typical sizes/weights if logged, and any notable patterns.

## By Spot / Location
Many entries record a specific spot or body of water in the "spot" field (e.g. "West Galveston Bay", "Cedar Lakes"). Break catches down by spot where it is recorded: which spots produced the most fish, which species at each, and any condition patterns specific to a spot. Note when the spot is blank/unknown rather than guessing.

## Best Windows to Fish {location_name}
Summarize the optimal conditions for planning a trip: time of day patterns, tide phase, pressure range, solunar alignment, and seasonal notes if detectable.

## Red Flags (When NOT to Go)
Conditions that consistently produced blanks or poor action.

## Next-Trip Recommendation
Given the historical patterns, write a concrete checklist an angler should verify before heading out to maximize success at {location_name}.

Be specific and data-driven. Note sample sizes. Keep it practical — this will be displayed on a fishing dashboard.

Data:
{json.dumps(entries, indent=2)}"""

    # ── Provider selection — set AI_PROVIDER in .env to switch ───────────────
    if AI_PROVIDER == "xai":
        # xAI / Grok — https://console.x.ai  (set XAI_API_KEY in .env)
        client = OpenAI(api_key=XAI_KEY, base_url="https://api.x.ai/v1")
        model  = model or ANALYSIS_MODEL or "grok-3-mini"
    else:
        # Groq (default) — https://console.groq.com  (set GROQ_API_KEY in .env)
        client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")
        model  = model or ANALYSIS_MODEL or "llama-3.3-70b-versatile"

    completion = client.chat.completions.create(
        model=model,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content

    # ── Gemini / Google (alternative) ─────────────────────────────────────────
    # To switch to Gemini:
    #   1. pip install google-genai>=1.0
    #   2. set GEMINI_API_KEY in .env  (use https://aistudio.google.com for free tier)
    #   3. uncomment below; comment out the provider-selection block above
    #
    # from google import genai as _genai
    # _client = _genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    # _resp = _client.models.generate_content(model="gemini-2.0-flash-lite", contents=prompt)
    # return _resp.text

    # ── Anthropic / Claude (alternative) ──────────────────────────────────────
    # To switch to Claude:
    #   1. pip install anthropic>=0.40
    #   2. set ANTHROPIC_API_KEY in .env  (https://console.anthropic.com)
    #   3. uncomment below; comment out the provider-selection block above
    #
    # from anthropic import Anthropic as _Anthropic
    # _ac = _Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # _msg = _ac.messages.create(model="claude-sonnet-4-6", max_tokens=3000,
    #                            messages=[{"role": "user", "content": prompt}])
    # return _msg.content[0].text

def save_analysis(location: str, content: str, window: str = "all",
                  model: str = "grok-3-mini" if os.environ.get("AI_PROVIDER","groq")=="xai" else "llama-3.3-70b-versatile"):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ai_analysis (location, analysis_type, content, model) VALUES (?,?,?,?)",
            (location, window, content, model),
        )
        conn.commit()

# ── Handwritten-log import via Claude vision ───────────────────────────────────
# Anthropic forces structured output by making the model call this "tool"; we read
# the validated JSON straight out of its input. One entry == one fish_log row.
IMPORT_TOOL = {
    "name": "record_log_entries",
    "description": "Record every fishing log entry transcribed from the handwritten page(s).",
    "input_schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "description": "One object per individual catch/trip line on the page.",
                "items": {
                    "type": "object",
                    "properties": {
                        "catch_date":  {"type": "string",
                                        "description": "Date of the catch in YYYY-MM-DD. Infer the year from page context if abbreviated."},
                        "catch_time":  {"type": ["string", "null"],
                                        "description": "Local time as HH:MM (24-hour), or null if not written."},
                        "species":     {"type": "string",
                                        "description": "Best match from the provided canonical species list. Use 'Other' if no reasonable match."},
                        "species_raw": {"type": ["string", "null"],
                                        "description": "Exactly what was written if it differs from the canonical name (abbreviations, slang)."},
                        "caught_location": {"type": ["string", "null"],
                                            "description": "The specific spot / body of water / area written on the page for this catch (e.g. 'West Galveston Bay', 'Cedar Lakes', 'Sargent jetty'). Null if none is written. Do NOT put GPS coordinates here."},
                        "caught":      {"type": "boolean",
                                        "description": "true if a fish was landed; false for an explicit skunk / no-catch line."},
                        "fish_count":  {"type": "integer", "description": "Number of this species caught on this line (default 1)."},
                        "size_in":     {"type": ["number", "null"], "description": "Length in inches if recorded."},
                        "weight_lbs":  {"type": ["number", "null"], "description": "Weight in pounds if recorded."},
                        "page_conditions": {"type": ["string", "null"],
                                            "description": "Any tide / weather / water conditions written on the page for this entry, verbatim."},
                        "bait":        {"type": ["string", "null"], "description": "Bait used (e.g. 'gulp shrimp', 'live croaker', 'topwater'). Null if not written."},
                        "tackle":      {"type": ["string", "null"], "description": "Tackle or technique (e.g. 'popping cork', 'jig head', 'Carolina rig'). Null if not written."},
                        "notes":       {"type": ["string", "null"], "description": "Any other remarks written for this entry."},
                        "confidence":  {"type": "number",
                                        "description": "Your transcription confidence for this line, 0.0–1.0."}
                    },
                    "required": ["catch_date", "species", "caught", "fish_count", "confidence"]
                }
            },
            "transcription": {"type": "string",
                              "description": "A faithful, literal transcription of all text on the page(s)."},
            "unreadable": {"type": ["string", "null"],
                           "description": "Note anything that was illegible or ambiguous so a human can review it."}
        },
        "required": ["entries", "transcription"]
    }
}

def extract_entries_from_images(images: list[tuple[str, bytes]], location: str,
                                model: str | None = None) -> dict:
    """Send page image(s) to Claude and get back structured fish_log entries.

    images: list of (media_type, raw_bytes). Returns the tool input dict
    ({"entries":[...], "transcription":..., "unreadable":...}).
    """
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set — handwritten-log import is disabled.")

    model = model or IMPORT_MODEL
    from anthropic import Anthropic  # lazy import: app boots fine without the package

    location_name = LOCATION_NAMES.get(location, location)
    species_list  = SPECIES.get(location, [])

    system = (
        "You are a meticulous data-entry assistant digitizing decades of handwritten "
        f"fishing logs for {location_name} on the US Gulf Coast.\n\n"
        "Transcribe EVERY catch line on the page into a structured entry. Rules:\n"
        "- Each fish (or each distinct line) becomes one entry. If a line records several "
        "of one species, set fish_count accordingly.\n"
        "- Map each species to the closest name in this canonical list; if nothing fits, "
        "use 'Other' and keep the original wording in species_raw:\n"
        f"{json.dumps(species_list)}\n"
        "- Anglers abbreviate (e.g. 'trout' = Spotted Seatrout, 'red'/'rat red' = Red Drum "
        "(Redfish), 'flounder' = Southern Flounder, 'sheepy' = Sheepshead). Use judgment.\n"
        "- A line noting a blank/skunk trip with no fish is a valid entry with caught=false.\n"
        "- If the page names a specific spot or body of water (e.g. 'West Galveston Bay', "
        "'Cedar Lakes', a named reef/jetty/cut), put it in caught_location — verbatim, one per "
        "entry. This is NOT GPS coordinates and NOT the general region.\n"
        "- Preserve tide, weather, bait, and water notes verbatim in page_conditions — do NOT "
        "invent values you cannot read.\n"
        "- Dates may be abbreviated; infer the full YYYY-MM-DD from context (column headers, "
        "running dates). If a year is genuinely unknowable, use your best estimate and flag it "
        "in 'unreadable'.\n"
        "- Lower your confidence for any line you are unsure about; never fabricate fish.\n"
        "Return results only by calling the record_log_entries tool."
    )

    content = []
    for media_type, raw in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(raw).decode("ascii"),
            },
        })
    content.append({
        "type": "text",
        "text": (f"These are handwritten fishing log page(s) for {location_name}. "
                 "Transcribe and structure every entry."),
    })

    client = Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=8000,
        # cache the static system prompt (instructions + species list) across pages
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=[IMPORT_TOOL],
        tool_choice={"type": "tool", "name": "record_log_entries"},
        messages=[{"role": "user", "content": content}],
    )

    for block in msg.content:
        if block.type == "tool_use" and block.name == "record_log_entries":
            return block.input
    raise RuntimeError("Claude returned no structured entries.")

# ── Free OCR path: read image → raw text, then structure with the text provider ─
def _ocr_tesseract(images: list[tuple[str, bytes]]) -> str:
    """On-device OCR with Tesseract. Free, no network. Best on neat printing."""
    import io
    import pytesseract
    from PIL import Image
    pages = []
    for i, (_, raw) in enumerate(images, 1):
        img = Image.open(io.BytesIO(raw))
        pages.append(f"--- page {i} ---\n" + pytesseract.image_to_string(img))
    return "\n\n".join(pages).strip()

def _prep_for_ocr_space(raw: bytes, max_px: int = 2200, target_bytes: int = 950_000) -> bytes:
    """Downscale/re-encode an image to fit OCR.space's 1 MB free-tier upload limit.

    Phone photos of a log page are routinely 5-7 MB and get rejected with HTTP 413,
    so shrink the longest edge and step JPEG quality down until it fits.
    """
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if max(img.size) > max_px:
        scale = max_px / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    for quality in (85, 75, 65, 55, 45):
        buf.seek(0); buf.truncate()
        img.save(buf, "JPEG", quality=quality)
        if buf.tell() <= target_bytes:
            break
    return buf.getvalue()

def _ocr_space(images: list[tuple[str, bytes]]) -> str:
    """Free online OCR via api.ocr.space (handwriting engine 2)."""
    pages = []
    for i, (_media_type, raw) in enumerate(images, 1):
        payload = _prep_for_ocr_space(raw)
        resp = requests.post(
            OCR_SPACE_URL,
            data={"apikey": OCR_SPACE_API_KEY, "OCREngine": "2", "scale": "true",
                  "isOverlayRequired": "false"},
            files={"file": (f"page{i}.jpg", payload, "image/jpeg")},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("IsErroredOnProcessing"):
            raise RuntimeError("OCR.space error: " + "; ".join(data.get("ErrorMessage") or ["unknown"]))
        parsed = "".join(r.get("ParsedText", "") for r in data.get("ParsedResults") or [])
        pages.append(f"--- page {i} ---\n{parsed}")
    return "\n\n".join(pages).strip()

def structure_text_to_entries(text: str, location: str, model: str | None = None) -> dict:
    """Turn raw OCR text into structured entries using the free text provider (Groq/xAI).

    OCR is noisy — the LLM also corrects obvious misreads using fishing context
    (species names, plausible dates/sizes). Returns {"entries":[...], "unreadable":...}.
    """
    if AI_PROVIDER == "xai":
        if not XAI_KEY:
            raise RuntimeError("XAI_API_KEY not set — needed to structure OCR text.")
        client = OpenAI(api_key=XAI_KEY, base_url="https://api.x.ai/v1")
        model = model or "grok-3-mini"
    else:
        if not GROQ_KEY:
            raise RuntimeError("GROQ_API_KEY not set — needed to structure OCR text. "
                               "Set it (free at console.groq.com) or use the Claude method.")
        client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")
        model = model or "llama-3.3-70b-versatile"

    if not text.strip():
        return {"entries": [], "unreadable": "OCR produced no text — the scan may be blank or too faint."}

    location_name = LOCATION_NAMES.get(location, location)
    species_list  = SPECIES.get(location, [])
    prompt = (
        f"You are digitizing scanned fishing records for {location_name} on the US Gulf Coast. "
        "Below is noisy OCR text from the scanned page(s), each delimited by \"--- page N ---\". "
        "Reconstruct the fishing entries as structured data, correcting obvious OCR errors using "
        "fishing context.\n\n"
        "A page may be EITHER a free-form handwritten log (many catch lines) OR a pre-printed "
        "CATCH / TAG FORM (e.g. a tournament or shark-tag card) where ONE form = ONE fish and the "
        "data sits in labeled fields such as Species, Total Length, Fork Length, Girth, Sex, Date, "
        "Angler Name, Tag #, Fish Condition, Tackle, Time, GPS. The printed field labels and "
        "instructions are NOT entries and are NOT a reason to skip a page — read the HANDWRITTEN "
        "values filled into the fields. EACH filled-in tag-form page is exactly one entry: never "
        "merge two pages into one, and never dismiss a filled-in form as a blank template.\n\n"
        "Return ONLY a JSON object of this exact shape:\n"
        '{"entries":[{"catch_date":"YYYY-MM-DD","catch_time":"HH:MM or null",'
        '"species":"canonical name","species_raw":"as written or null","caught":true,'
        '"fish_count":1,"size_in":null,"weight_lbs":null,'
        '"caught_location":"specific spot/body of water as written, or null",'
        '"bait":"bait used or null","tackle":"tackle/technique or null",'
        '"page_conditions":"tide/weather/water notes or null",'
        '"notes":"other remarks or null","confidence":0.0}],'
        '"unreadable":"note anything illegible, or null"}\n\n'
        "Rules:\n"
        "- One entry per catch line, or one entry per filled-in tag form. A blank/skunk trip with no "
        "fish is a valid entry with caught=false.\n"
        "- On a tag form, map Total Length to size_in (inches). Put Tackle in the tackle field. "
        "Keep other tag-form fields (Tag #, Fork Length, Girth, Sex, Angler Name, Fish Condition, GPS, Trip) "
        "in notes so nothing is lost.\n"
        "- If bait or lure is written (e.g. 'gulp shrimp', 'live shrimp', 'topwater', 'spoon'), put it in bait.\n"
        "- If tackle or technique is written (e.g. 'popping cork', 'jig head', 'Carolina rig', 'free-lined'), put it in tackle.\n"
        "- If a specific spot or body of water is written (e.g. 'West Galveston Bay', 'Cedar Lakes', "
        "a named reef/jetty/cut), put it in caught_location — not GPS coordinates, not the region.\n"
        f"- Map each species to the closest name in this list (use 'Other' if none fit):\n{json.dumps(species_list)}\n"
        "- Anglers abbreviate: 'trout'=Spotted Seatrout, 'red'/'rat red'=Red Drum (Redfish), "
        "'flounder'=Southern Flounder, 'sheepy'=Sheepshead.\n"
        "- Infer full YYYY-MM-DD dates from context (a 2-digit year like 22 -> 2022); convert am/pm "
        "times to 24-hour HH:MM. Lower confidence when unsure. Never invent fish.\n\n"
        f"OCR TEXT:\n{text}"
    )
    completion = client.chat.completions.create(
        model=model,
        max_tokens=4000,
        temperature=0,                       # deterministic structuring of noisy OCR
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    raw = completion.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {"entries": []}
    data.setdefault("entries", [])
    return data

def extract_log_entries(images: list[tuple[str, bytes]], location: str,
                        method: str, model: str | None = None) -> dict:
    """Dispatch to the chosen import method and normalize the result shape."""
    if method == "anthropic":
        result = extract_entries_from_images(images, location, model=model)
        result["ocr_method"] = "anthropic"
        return result

    if method == "ocr_space":
        text = _ocr_space(images)
    else:                                    # default: tesseract
        method = "tesseract"
        text = _ocr_tesseract(images)

    result = structure_text_to_entries(text, location, model=model)
    result["transcription"] = text
    result["ocr_method"] = method
    return result

def import_methods_available() -> dict:
    """Which import methods are usable given the configured keys."""
    text_ok = bool(GROQ_KEY or XAI_KEY)      # needed to structure OCR text
    return {
        "tesseract": text_ok,
        "ocr_space": text_ok,
        "anthropic": bool(ANTHROPIC_KEY),
    }

def _entry_dt(entry: dict) -> datetime:
    """Combine an extracted entry's date + time into a tz-aware datetime."""
    raw = (entry.get("catch_date") or "").strip()
    tm  = (entry.get("catch_time") or "12:00").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{raw} {tm}", fmt).replace(tzinfo=APP_TZ)
        except ValueError:
            continue
    # fall back to date-only at noon
    return datetime.strptime(raw, "%Y-%m-%d").replace(hour=12, tzinfo=APP_TZ)

def insert_imported_entry(conn, location: str, entry: dict, cond_cache: dict | None = None) -> None:
    """Insert one reviewed import entry, backfilling historical conditions for its date.

    Conditions the angler wrote on the page are preserved in `notes`; the numeric
    columns are backfilled from the historical exporter (NOT today's live weather)
    so imported rows analyze correctly. `cond_cache` memoizes by date so several
    catches on the same day cost a single lookup during a bulk import.
    """
    catch_dt = _entry_dt(entry)
    key = catch_dt.date().isoformat()
    if cond_cache is not None and key in cond_cache:
        cond = cond_cache[key]
    else:
        try:                                     # historical backfill is best-effort
            cond = get_conditions_for_date(location, catch_dt, allow_live_fallback=False)
        except Exception as e:
            log.warning("Condition backfill failed for %s: %s", key, e)
            cond = {}
        if cond_cache is not None:
            cond_cache[key] = cond

    note_parts = []
    if entry.get("notes"):           note_parts.append(str(entry["notes"]).strip())
    if entry.get("page_conditions"): note_parts.append(f"Page conditions: {entry['page_conditions']}")
    if entry.get("species_raw") and entry["species_raw"] != entry.get("species"):
        note_parts.append(f"Logged as: {entry['species_raw']}")
    note_parts.append("[imported from handwritten log]")
    notes = " — ".join(p for p in note_parts if p) or None

    caught_location = (entry.get("caught_location") or "").strip() or None

    conn.execute("""
        INSERT INTO fish_log (
            logged_at,
            location, caught_location, species, caught, fish_count, size_in, weight_lbs,
            bait, tackle, notes,
            tide_height_ft, tide_stage, water_level_ft,
            pressure_mb, pressure_trend, temp_f, wind_speed_mph, wind_deg,
            precip_chance, humidity, cloud_cover,
            solunar_period, moon_phase_pct, fishing_score
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        int(catch_dt.timestamp()),
        location, caught_location, entry.get("species") or "Other",
        1 if entry.get("caught") else 0,
        int(entry.get("fish_count") or 1),
        entry.get("size_in"), entry.get("weight_lbs"),
        (entry.get("bait") or "").strip() or None,
        (entry.get("tackle") or "").strip() or None,
        notes,
        cond.get("tide_height_ft"), cond.get("tide_stage"), cond.get("water_level_ft"),
        cond.get("pressure_mb"), cond.get("pressure_trend"),
        cond.get("temp_f"), cond.get("wind_speed_mph"), cond.get("wind_deg"),
        cond.get("precip_chance"), cond.get("humidity"), cond.get("cloud_cover"),
        cond.get("solunar_period"), cond.get("moon_phase_pct"), cond.get("fishing_score"),
    ))

def analysis_scheduler():
    time.sleep(90)  # let stack stabilize before first run
    while True:
        for location in SPECIES:
            try:
                log.info("Running AI analysis for %s", location)
                content = run_ai_analysis(location, window="all")
                save_analysis(location, content, window="all")
                log.info("AI analysis saved for %s", location)
            except Exception as exc:
                log.error("AI analysis failed for %s: %s", location, exc)
        time.sleep(ANALYSIS_HOURS * 3600)

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    location = request.args.get("location", "freeport_tx")
    logged   = request.args.get("logged")
    cond     = get_conditions(location)
    return render_template("index.html",
                           locations=LOCATION_NAMES,
                           species=SPECIES,
                           selected=location,
                           cond=cond,
                           logged=logged)

def _parse_catch_dt(raw: str) -> datetime:
    """Parse datetime-local string to timezone-aware datetime; fall back to now."""
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=APP_TZ)
    except Exception:
        return datetime.now(tz=APP_TZ)


@app.route("/log", methods=["POST"])
def log_catch():
    f        = request.form
    location = f.get("location", "freeport_tx")
    catch_dt = _parse_catch_dt(f.get("catch_datetime", ""))
    cond     = get_conditions_for_date(location, catch_dt)

    size   = float(f["size_in"])    if f.get("size_in")    else None
    weight = float(f["weight_lbs"]) if f.get("weight_lbs") else None
    count  = int(f.get("fish_count") or 1)
    caught_location = (f.get("caught_location") or "").strip() or None
    bait   = (f.get("bait") or "").strip() or None
    tackle = (f.get("tackle") or "").strip() or None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO fish_log (
                logged_at,
                location, caught_location, species, caught, fish_count, size_in, weight_lbs,
                bait, tackle, notes,
                tide_height_ft, tide_stage, water_level_ft,
                pressure_mb, pressure_trend, temp_f, wind_speed_mph, wind_deg,
                precip_chance, humidity, cloud_cover,
                solunar_period, moon_phase_pct, fishing_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(catch_dt.timestamp()),
            location, caught_location, f.get("species"), 1 if f.get("caught") == "yes" else 0,
            count, size, weight, bait, tackle, f.get("notes") or None,
            cond.get("tide_height_ft"), cond.get("tide_stage"), cond.get("water_level_ft"),
            cond.get("pressure_mb"), cond.get("pressure_trend"),
            cond.get("temp_f"), cond.get("wind_speed_mph"), cond.get("wind_deg"),
            cond.get("precip_chance"), cond.get("humidity"), cond.get("cloud_cover"),
            cond.get("solunar_period"), cond.get("moon_phase_pct"), cond.get("fishing_score"),
        ))
        conn.commit()

    return redirect(url_for("index", location=location, logged="1"))

@app.route("/history")
def history():
    location = request.args.get("location", "freeport_tx")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM fish_log WHERE location=? ORDER BY logged_at DESC LIMIT 100",
            (location,),
        ).fetchall()
    return render_template("history.html",
                           locations=LOCATION_NAMES,
                           selected=location,
                           rows=rows)

@app.route("/analysis")
def analysis():
    location = request.args.get("location", "freeport_tx")
    window   = request.args.get("window", "all")
    if window not in ANALYSIS_WINDOWS:
        window = "all"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute(
            "SELECT * FROM ai_analysis WHERE location=? AND analysis_type=? ORDER BY generated_at DESC LIMIT 1",
            (location, window),
        ).fetchone()
        if latest is None and window != "all":
            latest = conn.execute(
                "SELECT * FROM ai_analysis WHERE location=? ORDER BY generated_at DESC LIMIT 1",
                (location,),
            ).fetchone()
    return render_template("analysis.html",
                           locations=LOCATION_NAMES,
                           selected=location,
                           window=window,
                           windows=ANALYSIS_WINDOWS,
                           latest=latest)

@app.route("/api/recent")
def api_recent():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, logged_at, location, species, caught FROM fish_log ORDER BY logged_at DESC LIMIT 20"
        ).fetchall()
    tz = ZoneInfo("America/Chicago")
    return jsonify([{
        "id":       r["id"],
        "time":     datetime.fromtimestamp(r["logged_at"], tz=tz).strftime("%m/%d %H:%M"),
        "location": r["location"],
        "species":  r["species"],
        "caught":   bool(r["caught"]),
    } for r in rows])

@app.route("/api/log/<int:entry_id>", methods=["DELETE", "POST"])
def delete_log(entry_id):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("DELETE FROM fish_log WHERE id=?", (entry_id,)).rowcount
        conn.commit()
    if rows:
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "entry not found"}), 404

@app.route("/api/log/bulk-delete", methods=["POST", "OPTIONS"])
def bulk_delete_log():
    if request.method == "OPTIONS":
        return "", 204
    ids = request.json.get("ids", []) if request.is_json else []
    if not ids or not isinstance(ids, list):
        return jsonify({"status": "error", "message": "ids must be a non-empty list"}), 400
    ids = [int(i) for i in ids if str(i).lstrip("-").isdigit()]
    with sqlite3.connect(DB_PATH) as conn:
        deleted = conn.execute(
            f"DELETE FROM fish_log WHERE id IN ({','.join('?' for _ in ids)})", ids
        ).rowcount
        conn.commit()
    return jsonify({"status": "ok", "deleted": deleted})

@app.route("/api/analysis/<location>")
def api_analysis(location):
    window = request.args.get("window", "all")
    if window not in ANALYSIS_WINDOWS:
        window = "all"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ai_analysis WHERE location=? AND analysis_type=? ORDER BY generated_at DESC LIMIT 1",
            (location, window),
        ).fetchone()
    if row:
        return jsonify({"status":"ok","content":row["content"],
                        "generated":row["generated_at"],"model":row["model"],
                        "window":window})
    return jsonify({"status":"ok","content":None,"window":window})

@app.route("/api/analyze/<location>", methods=["POST"])
def api_analyze(location):
    window = request.args.get("window", "all")
    if window not in ANALYSIS_WINDOWS:
        window = "all"
    model = request.args.get("model") or None
    try:
        content = run_ai_analysis(location, window, model=model)
        save_analysis(location, content, window)
        return jsonify({"status": "ok", "content": content})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route("/api/conditions/<location>")
def api_conditions(location):
    date_str = request.args.get("date")
    if date_str:
        try:
            target_dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M").replace(tzinfo=APP_TZ)
            return jsonify(get_conditions_for_date(location, target_dt))
        except ValueError:
            pass
    return jsonify(get_conditions(location))

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["X-Frame-Options"]              = "ALLOWALL"
    return response

@app.route("/api/log", methods=["POST", "OPTIONS"])
def api_log():
    if request.method == "OPTIONS":
        return "", 204
    f        = request.form
    location = f.get("location", "freeport_tx")
    catch_dt = _parse_catch_dt(f.get("catch_datetime", ""))
    cond     = get_conditions_for_date(location, catch_dt)
    size     = float(f["size_in"])    if f.get("size_in")    else None
    weight   = float(f["weight_lbs"]) if f.get("weight_lbs") else None
    caught_location = (f.get("caught_location") or "").strip() or None
    bait   = (f.get("bait") or "").strip() or None
    tackle = (f.get("tackle") or "").strip() or None
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO fish_log (
                logged_at,
                location, caught_location, species, caught, fish_count, size_in, weight_lbs,
                bait, tackle, notes,
                tide_height_ft, tide_stage, water_level_ft,
                pressure_mb, pressure_trend, temp_f, wind_speed_mph, wind_deg,
                precip_chance, humidity, cloud_cover,
                solunar_period, moon_phase_pct, fishing_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(catch_dt.timestamp()),
            location, caught_location, f.get("species"), 1 if f.get("caught") == "yes" else 0,
            int(f.get("fish_count") or 1), size, weight, bait, tackle, f.get("notes") or None,
            cond.get("tide_height_ft"), cond.get("tide_stage"), cond.get("water_level_ft"),
            cond.get("pressure_mb"), cond.get("pressure_trend"),
            cond.get("temp_f"), cond.get("wind_speed_mph"), cond.get("wind_deg"),
            cond.get("precip_chance"), cond.get("humidity"), cond.get("cloud_cover"),
            cond.get("solunar_period"), cond.get("moon_phase_pct"), cond.get("fishing_score"),
        ))
        conn.commit()
    return jsonify({"status": "ok"})

# ── Handwritten-log import routes ──────────────────────────────────────────────
@app.route("/import")
def import_page():
    location  = request.args.get("location", "freeport_tx")
    available = import_methods_available()
    default   = OCR_PROVIDER_DEFAULT if available.get(OCR_PROVIDER_DEFAULT) else \
                next((m for m, ok in available.items() if ok), OCR_PROVIDER_DEFAULT)
    return render_template("import.html",
                           locations=LOCATION_NAMES,
                           species=SPECIES,
                           selected=location,
                           import_enabled=any(available.values()),
                           methods=OCR_METHODS,
                           methods_available=available,
                           default_method=default,
                           model=IMPORT_MODEL)

@app.route("/api/import/extract", methods=["POST", "OPTIONS"])
def api_import_extract():
    """Accept uploaded page image(s), return Claude-extracted entries for review.

    Nothing is written to the database here — the user reviews/edits the proposed
    entries client-side, then POSTs them to /api/import/commit.
    """
    if request.method == "OPTIONS":
        return "", 204

    location = request.form.get("location", "freeport_tx")
    method   = (request.form.get("ocr") or OCR_PROVIDER_DEFAULT).lower()
    if method not in OCR_METHODS:
        return jsonify({"status": "error", "message": f"Unknown OCR method: {method}"}), 400
    if not import_methods_available().get(method):
        need = "ANTHROPIC_API_KEY" if method == "anthropic" else "GROQ_API_KEY (or XAI_API_KEY)"
        return jsonify({"status": "error", "message": f"Method '{method}' unavailable — {need} not set."}), 503
    model = request.form.get("model") or None
    files = request.files.getlist("images")
    if not files:
        return jsonify({"status": "error", "message": "No images uploaded."}), 400
    if len(files) > IMPORT_MAX_IMAGES:
        return jsonify({"status": "error",
                        "message": f"Too many images (max {IMPORT_MAX_IMAGES})."}), 400

    images = []
    for fs in files:
        mt = (fs.mimetype or "").lower()
        if mt not in ALLOWED_IMG_TYPES:
            return jsonify({"status": "error",
                            "message": f"Unsupported file type: {fs.filename} ({mt or 'unknown'})."}), 400
        images.append((mt, fs.read()))

    try:
        result = extract_log_entries(images, location, method, model=model)
    except Exception as e:
        log.exception("Import extraction failed")
        msg = str(e)
        msg_l = msg.lower()
        # A 429 rate-limit is transient (retryable) — never fatal. Guard this first
        # because Groq's rate-limit message embeds a "…/settings/billing" upgrade URL
        # that would otherwise trip the "billing" keyword below and abort the whole run.
        rate_limited = any(k in msg_l for k in
                           ("rate_limit", "rate limit", "too many requests", " 429", "429 "))
        # Permanent problems (bad/no key, no credits, no model access) won't fix
        # themselves — flag them 402 so the bulk CLI aborts instead of retrying
        # the same error on every page. Everything else is treated as transient.
        fatal = (not rate_limited) and any(k in msg_l for k in
                    ("credit balance", "billing", "authentication", "x-api-key",
                     "permission", "not_found_error", "invalid api key", "not set"))
        return jsonify({"status": "error", "message": msg, "fatal": fatal}), (402 if fatal else 502)

    entries = result.get("entries", []) if isinstance(result, dict) else []
    return jsonify({
        "status": "ok",
        "location": location,
        "method": result.get("ocr_method", method) if isinstance(result, dict) else method,
        "pages": len(images),
        "entries": entries,
        "transcription": result.get("transcription", "") if isinstance(result, dict) else "",
        "unreadable": result.get("unreadable") if isinstance(result, dict) else None,
    })

@app.route("/api/import/commit", methods=["POST", "OPTIONS"])
def api_import_commit():
    """Persist the reviewed entries. Body: {location, entries:[...]}."""
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    location = data.get("location", "freeport_tx")
    entries  = data.get("entries", [])
    if not isinstance(entries, list) or not entries:
        return jsonify({"status": "error", "message": "No entries to import."}), 400

    inserted, errors = 0, []
    cond_cache: dict = {}        # memoize conditions per date across this batch
    with sqlite3.connect(DB_PATH) as conn:
        for i, entry in enumerate(entries):
            try:
                insert_imported_entry(conn, location, entry, cond_cache)
                inserted += 1
            except Exception as e:
                errors.append({"index": i, "message": str(e),
                               "species": entry.get("species"), "date": entry.get("catch_date")})
        conn.commit()

    return jsonify({"status": "ok", "inserted": inserted, "errors": errors})

@app.route("/tides")
def tides():
    today = datetime.now(tz=APP_TZ).strftime("%Y-%m-%d")
    return render_template("tides.html", locations=LOCATION_NAMES,
                           station_ids=NOAA_TIDE_STATION_IDS, today=today)

@app.route("/api/stations/search")
def api_stations_search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])
    try:
        stations = _get_noaa_stations()
    except Exception as exc:
        log.warning("NOAA station list fetch failed: %s", exc)
        return jsonify({"error": "Could not load NOAA station list"}), 502
    results = [
        s for s in stations
        if q in s["name"].lower() or q in s.get("state", "").lower()
    ][:20]
    return jsonify(results)

@app.route("/api/tides/station")
def api_tides_station():
    station_id = request.args.get("id", "").strip()
    date_str   = request.args.get("date", datetime.now(tz=APP_TZ).strftime("%Y-%m-%d"))
    if not station_id:
        return jsonify({"error": "station id required"}), 400
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "invalid date, expected YYYY-MM-DD"}), 400
    noaa_date = date_obj.strftime("%Y%m%d")
    try:
        r = requests.get(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
            params={
                "product":     "predictions",
                "station":     station_id,
                "datum":       "MLLW",
                "time_zone":   "lst_ldt",
                "interval":    "hilo",
                "units":       "english",
                "application": "fishing_dashboard",
                "format":      "json",
                "begin_date":  noaa_date,
                "end_date":    noaa_date,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return jsonify({"error": data["error"].get("message", "NOAA returned an error")}), 400
        return jsonify({"predictions": data.get("predictions", [])})
    except Exception as exc:
        log.warning("NOAA tide fetch failed for station %s: %s", station_id, exc)
        return jsonify({"error": str(exc)}), 502

# ── Maps: wind (Windy) + salinity (NOAA NGOFS2 OFS) ──────────────────────────
# Surface-salinity forecast plots from NOAA's Northern Gulf OFS, looped like the
# Tides mobile app. Region is auto-picked nearest the station; NGOFS2 only
# models the northern Gulf, so non-Gulf stations get a coverage notice instead.
_NGOFS2_REGIONS = [
    ("gb",  "Galveston Bay",            29.4, -94.9),
    ("ma",  "Matagorda Bay",            28.5, -96.2),
    ("cc",  "Corpus Christi",           27.8, -97.1),
    ("sn",  "Sabine–Neches",            29.7, -93.9),
    ("lc",  "Calcasieu / Lake Charles", 29.9, -93.3),
    ("lp",  "Lake Pontchartrain",       30.1, -90.1),
    ("gf",  "Gulfport",                 30.3, -89.1),
    ("pg",  "Pascagoula",               30.3, -88.5),
    ("mb",  "Mobile Bay",               30.4, -88.0),
    ("gom", "Gulf of America (overview)", 27.5, -90.5),
]
_OFS_CDN = "https://cdn.tidesandcurrents.noaa.gov/ofs/ngofs2/wwwgraphics"
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_sal_cache: dict = {}        # region_code -> (ts, frames)
_sal_lock = threading.Lock()

def _station_coords(station_id: str):
    for s in _get_noaa_stations():
        if s["id"] == station_id:
            try:
                return float(s["lat"]), float(s["lng"])
            except (TypeError, ValueError):
                break
    return None, None

def _nearest_ngofs2_region(lat: float, lon: float):
    best, best_d = None, 1e9
    for code, label, rlat, rlon in _NGOFS2_REGIONS:
        if code == "gom":
            continue  # overview center is offshore — never the auto-pick
        d = (rlat - lat) ** 2 + (rlon - lon) ** 2
        if d < best_d:
            best, best_d = (code, label), d
    return best[0], best[1], best_d ** 0.5

def _fmt_ofs_label(raw: str) -> str:
    # "1000 (CDT) 05/30/26" -> "10:00 CDT · 05/30"
    m = re.match(r"^(\d{2})(\d{2})\s*\(([^)]+)\)\s*(\d{2})/(\d{2})", raw)
    return f"{m.group(1)}:{m.group(2)} {m.group(3)} · {m.group(4)}/{m.group(5)}" if m else raw

def _salinity_frames(region_code: str) -> list:
    now = time.time()
    cached = _sal_cache.get(region_code)
    if cached and now - cached[0] < 1800:
        return cached[1]
    with _sal_lock:
        cached = _sal_cache.get(region_code)
        if cached and now - cached[0] < 1800:
            return cached[1]
        # Fetch the option file server-side (the CDN blocks browser fetch via
        # CORS, and rejects requests without a browser UA).
        r = requests.get(f"{_OFS_CDN}/NGOFS2_{region_code}_all_sa_fore_option",
                         headers={"User-Agent": _BROWSER_UA}, timeout=15)
        r.raise_for_status()
        frames = []
        for m in re.finditer(r'value="(?:model_graphics/)?([^"]+\.png)"[^>]*>([^<\r\n]+)', r.text):
            fname = m.group(1).split("/")[-1]
            frames.append({"url": f"{_OFS_CDN}/{fname}", "label": _fmt_ofs_label(m.group(2).strip())})
        _sal_cache[region_code] = (now, frames)
        return frames

@app.route("/map/salinity")
def map_salinity():
    """Standalone full-page salinity loop (opened in a new tab from buttons)."""
    return render_template("salinity_map.html",
                           station_id=request.args.get("id", "").strip(),
                           name=request.args.get("name", "Station"))

@app.route("/api/maps/<station_id>")
def api_maps(station_id):
    """Coords + salinity-loop frames for a station's Wind/Salinity map buttons."""
    lat, lon = _station_coords(station_id.strip())
    if lat is None:
        return jsonify({"error": "station coordinates not found"}), 404
    code, label, dist = _nearest_ngofs2_region(lat, lon)
    sal = {"in_coverage": dist <= 4.0, "region_code": code, "region_label": label}
    if sal["in_coverage"]:
        try:
            sal["frames"] = _salinity_frames(code)
        except Exception as exc:
            log.warning("salinity frames fetch failed (%s): %s", code, exc)
            sal["frames"], sal["error"] = [], "Could not load salinity forecast"
    return jsonify({"lat": lat, "lon": lon, "salinity": sal})

@app.route("/api/weather")
def api_weather():
    """Fetch NWS weather for arbitrary lat/lng — used by the Grafana station search panel."""
    lat_s  = request.args.get("lat", "").strip()
    lng_s  = request.args.get("lng", "").strip()
    date_s = request.args.get("date", datetime.now(tz=APP_TZ).strftime("%Y-%m-%d"))
    if not lat_s or not lng_s:
        return jsonify({"error": "lat and lng required"}), 400
    try:
        lat_f    = float(lat_s)
        lng_f    = float(lng_s)
        date_obj = datetime.strptime(date_s, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid parameters"}), 400

    try:
        grid = _get_nws_grid(lat_f, lng_f)
    except Exception as exc:
        return jsonify({"error": "NWS grid lookup failed: " + str(exc)}), 502

    if not grid:
        return jsonify({"error": "Location outside NWS coverage area"}), 404

    try:
        wx = _fetch_nws_weather_for_date(grid, date_obj)
    except Exception as exc:
        return jsonify({"error": "Weather fetch failed: " + str(exc)}), 502

    return jsonify(wx)


# ── NWS grid cache: {"{lat},{lng}" -> {office,gridX,gridY,obs_station}} ──────
_nws_grid_cache: dict = {}
_nws_grid_lock  = threading.Lock()
NWS_UA = {"User-Agent": "FishingDashboard/1.0 (fishing-dashboard@localhost)"}


def _get_nws_grid(lat: float, lng: float) -> dict | None:
    key = f"{lat:.4f},{lng:.4f}"
    with _nws_grid_lock:
        if key in _nws_grid_cache:
            return _nws_grid_cache[key]

    try:
        r = requests.get(
            f"https://api.weather.gov/points/{lat:.4f},{lng:.4f}",
            headers=NWS_UA, timeout=12,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        props = r.json().get("properties", {})
        office = props.get("cwa")
        gridX  = props.get("gridX")
        gridY  = props.get("gridY")
        stations_url = props.get("observationStations")
        if not (office and gridX is not None and gridY is not None):
            return None

        # Get nearest obs station
        obs_station = None
        if stations_url:
            try:
                rs = requests.get(stations_url, headers=NWS_UA, timeout=10)
                rs.raise_for_status()
                feats = rs.json().get("features", [])
                if feats:
                    obs_station = feats[0]["properties"]["stationIdentifier"]
            except Exception:
                pass

        grid = {"office": office, "gridX": gridX, "gridY": gridY, "obs_station": obs_station}
        with _nws_grid_lock:
            _nws_grid_cache[key] = grid
        return grid
    except Exception as exc:
        log.warning("NWS /points lookup failed for %s,%s: %s", lat, lng, exc)
        return None


def _nws_val(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get("value")
        return None if v is None else float(v)
    return float(obj)


def _fetch_nws_weather_for_date(grid: dict, target: date) -> dict:
    today       = date.today()
    obs_station = grid.get("obs_station")
    office, gx, gy = grid["office"], grid["gridX"], grid["gridY"]

    # ── Helper: convert C→F ──────────────────────────────────────────────
    def c_to_f(c):
        return round(c * 9 / 5 + 32, 1) if c is not None else None

    def pa_to_mb(pa):
        return round(pa / 100, 1) if pa is not None else None

    def kph_to_mph(k):
        return round(k * 0.621371, 1) if k is not None else None

    # ── Feels-like (wind chill / heat index) ────────────────────────────
    def feels_like(temp_f, wind_mph, humidity):
        if temp_f is None:
            return None
        if wind_mph and temp_f <= 50 and wind_mph >= 3:
            return round(35.74 + 0.6215 * temp_f
                         - 35.75 * wind_mph ** 0.16
                         + 0.4275 * temp_f * wind_mph ** 0.16, 1)
        if humidity and temp_f >= 80:
            hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * humidity
                  - 0.22475541 * temp_f * humidity - 0.00683783 * temp_f ** 2
                  - 0.05481717 * humidity ** 2 + 0.00122874 * temp_f ** 2 * humidity
                  + 0.00085282 * temp_f * humidity ** 2
                  - 0.00000199 * temp_f ** 2 * humidity ** 2)
            return round(hi, 1)
        return round(temp_f, 1)

    # ── Cloud % from okta layers ────────────────────────────────────────
    okta = {"FEW": 19, "SCT": 44, "BKN": 75, "OVC": 100, "VV": 100}

    # ── TODAY / PAST: use observation ───────────────────────────────────
    if target <= today and obs_station:
        try:
            r = requests.get(
                f"https://api.weather.gov/stations/{obs_station}/observations/latest",
                headers=NWS_UA, timeout=12,
            )
            r.raise_for_status()
            obs = r.json().get("properties", {})
            temp_c   = _nws_val(obs.get("temperature"))
            temp_f   = c_to_f(temp_c)
            hum      = _nws_val(obs.get("relativeHumidity"))
            hum      = round(hum) if hum is not None else None

            # pressure: prefer seaLevelPressure, fall back to barometricPressure
            pres_pa  = _nws_val(obs.get("seaLevelPressure"))
            if pres_pa is None:
                pres_pa = _nws_val(obs.get("barometricPressure"))
            pres_mb  = pa_to_mb(pres_pa)

            ws_obj   = obs.get("windSpeed")
            ws_val   = _nws_val(ws_obj)
            ws_unit  = (ws_obj or {}).get("unitCode", "") if isinstance(ws_obj, dict) else ""
            if ws_val is not None:
                wind_mph = kph_to_mph(ws_val) if "km" in ws_unit else round(ws_val * 2.23694, 1)
            else:
                wind_mph = None

            wind_deg = _nws_val(obs.get("windDirection"))
            wind_deg = round(wind_deg) if wind_deg is not None else None

            cloud_pct = 0
            for layer in obs.get("cloudLayers", []):
                cloud_pct = max(cloud_pct, okta.get(layer.get("amount", ""), 0))

            vis_m  = _nws_val(obs.get("visibility"))
            vis_mi = round(vis_m * 0.000621371, 1) if vis_m is not None else None

            desc = obs.get("textDescription", "")

            return {
                "temp_f":     temp_f,
                "feels_f":    feels_like(temp_f, wind_mph, hum),
                "pressure_mb": pres_mb,
                "humidity":   hum,
                "wind_mph":   wind_mph,
                "wind_deg":   wind_deg,
                "precip_pct": 0,
                "cloud_pct":  cloud_pct,
                "vis_mi":     vis_mi,
                "description": desc,
            }
        except Exception as exc:
            log.warning("NWS obs fetch failed %s: %s", obs_station, exc)

    # ── FUTURE (≤7 days): hourly forecast ──────────────────────────────
    try:
        r = requests.get(
            f"https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast/hourly",
            headers=NWS_UA, timeout=12,
        )
        r.raise_for_status()
        periods = r.json().get("properties", {}).get("periods", [])
        noon_dt = datetime(target.year, target.month, target.day, 12)
        best, best_diff = None, float("inf")
        for p in periods:
            try:
                start = datetime.fromisoformat(p["startTime"].replace("Z", "+00:00"))
                start = start.replace(tzinfo=None)
                diff  = abs((start - noon_dt).total_seconds())
                if diff < best_diff:
                    best_diff, best = diff, p
            except Exception:
                pass
        if not best:
            return {}
        ws_str = best.get("windSpeed", "")
        try:
            wind_mph = float(ws_str.split()[0]) if ws_str else None
        except Exception:
            wind_mph = None
        sf = best.get("shortForecast", "").lower()
        if "overcast" in sf or ("cloudy" in sf and "partly" not in sf and "mostly" not in sf):
            cloud_pct = 90
        elif "mostly cloudy" in sf:
            cloud_pct = 70
        elif "partly" in sf:
            cloud_pct = 40
        elif "mostly clear" in sf or "mostly sunny" in sf:
            cloud_pct = 15
        else:
            cloud_pct = 5
        temp_f   = float(best["temperature"]) if best.get("temperature") is not None else None
        hum      = (best.get("relativeHumidity") or {}).get("value")
        hum      = int(hum) if hum is not None else None
        precip   = (best.get("probabilityOfPrecipitation") or {}).get("value") or 0
        return {
            "temp_f":      temp_f,
            "feels_f":     feels_like(temp_f, wind_mph, hum),
            "pressure_mb": None,
            "humidity":    hum,
            "wind_mph":    wind_mph,
            "wind_deg":    None,
            "precip_pct":  int(precip),
            "cloud_pct":   cloud_pct,
            "vis_mi":      None,
            "description": best.get("shortForecast", ""),
        }
    except Exception as exc:
        log.warning("NWS forecast fetch failed %s/%s,%s: %s", office, gx, gy, exc)
        return {}


@app.route("/embed")
def embed():
    """Self-contained log form for Grafana iframe — no navbar, pure AJAX, no page navigation."""
    return render_template("embed.html", species=SPECIES, locations=LOCATION_NAMES)

# ── Database export ────────────────────────────────────────────────────────────
@app.route("/api/export/fish_log.csv")
def export_fish_log_csv():
    """Download the full catch log as CSV (opens in Excel/Sheets). Optional ?location= filter."""
    import csv
    import io
    location = request.args.get("location", "").strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if location:
            rows = conn.execute("SELECT * FROM fish_log WHERE location=? ORDER BY logged_at DESC",
                                (location,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM fish_log ORDER BY logged_at DESC").fetchall()
        # column order is stable even when there are no rows
        cols = [r[1] for r in conn.execute("PRAGMA table_info(fish_log)")]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])

    stamp = datetime.now(tz=APP_TZ).strftime("%Y%m%d")
    fname = f"fish_log_{location + '_' if location else ''}{stamp}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/api/export/fish_log.db")
def export_fish_log_db():
    """Download the entire SQLite database as a consistent file (uses the online backup API
    so an in-flight write can't corrupt the copy)."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(tmp.name)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    stamp = datetime.now(tz=APP_TZ).strftime("%Y%m%d")
    return send_file(tmp.name, as_attachment=True,
                     download_name=f"fish_log_{stamp}.db",
                     mimetype="application/x-sqlite3")


# ── Stats / analytics ─────────────────────────────────────────────────────────
@app.route("/stats")
def stats():
    location = request.args.get("location", "freeport_tx")
    return render_template("stats.html", locations=LOCATION_NAMES, selected=location)

@app.route("/api/stats/<location>")
def api_stats(location):
    if location not in LOCATION_NAMES:
        return jsonify({"error": "unknown location"}), 404
    tz = ZoneInfo("America/Chicago")
    cutoff_12mo = int((datetime.now(tz=tz) - timedelta(days=365)).timestamp())

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # ── Species breakdown (caught only) ──────────────────────────────────
        species_rows = conn.execute("""
            SELECT species,
                   COUNT(*) AS entries,
                   SUM(fish_count) AS total_fish
            FROM fish_log WHERE location=? AND caught=1
            GROUP BY species ORDER BY total_fish DESC LIMIT 12
        """, (location,)).fetchall()

        # ── Monthly totals (last 12 months) ──────────────────────────────────
        monthly_rows = conn.execute("""
            SELECT strftime('%Y-%m', datetime(logged_at,'unixepoch','localtime')) AS month,
                   SUM(CASE WHEN caught=1 THEN COALESCE(fish_count,1) ELSE 0 END) AS fish,
                   COUNT(*) AS trips
            FROM fish_log WHERE location=? AND logged_at >= ?
            GROUP BY month ORDER BY month
        """, (location, cutoff_12mo)).fetchall()

        # ── Solunar success rate ──────────────────────────────────────────────
        sol_rows = conn.execute("""
            SELECT COALESCE(solunar_period,'none') AS period,
                   COUNT(*) AS total,
                   SUM(caught) AS caught
            FROM fish_log WHERE location=?
            GROUP BY period
        """, (location,)).fetchall()

        # ── Pressure range buckets vs success ────────────────────────────────
        pres_rows = conn.execute("""
            SELECT CASE
                     WHEN pressure_mb < 1010 THEN 'Under 1010'
                     WHEN pressure_mb < 1015 THEN '1010-1015'
                     WHEN pressure_mb < 1020 THEN '1015-1020'
                     ELSE 'Over 1020'
                   END AS bucket,
                   COUNT(*) AS total,
                   SUM(caught) AS caught
            FROM fish_log WHERE location=? AND pressure_mb IS NOT NULL
            GROUP BY bucket
        """, (location,)).fetchall()

        # ── Personal bests per species ────────────────────────────────────────
        bests_rows = conn.execute("""
            SELECT species,
                   MAX(size_in) AS max_size,
                   MAX(weight_lbs) AS max_weight
            FROM fish_log WHERE location=? AND caught=1
              AND (size_in IS NOT NULL OR weight_lbs IS NOT NULL)
            GROUP BY species
            ORDER BY MAX(size_in) DESC
        """, (location,)).fetchall()

        # ── Top spots ─────────────────────────────────────────────────────────
        spots_rows = conn.execute("""
            SELECT caught_location AS spot,
                   COUNT(*) AS total,
                   SUM(caught) AS caught,
                   SUM(CASE WHEN caught=1 THEN COALESCE(fish_count,1) ELSE 0 END) AS fish
            FROM fish_log
            WHERE location=? AND caught_location IS NOT NULL AND caught_location != ''
            GROUP BY caught_location ORDER BY fish DESC LIMIT 10
        """, (location,)).fetchall()

        # ── Summary totals ────────────────────────────────────────────────────
        totals = conn.execute("""
            SELECT COUNT(*) AS entries,
                   SUM(caught) AS caught_trips,
                   SUM(CASE WHEN caught=1 THEN COALESCE(fish_count,1) ELSE 0 END) AS total_fish
            FROM fish_log WHERE location=?
        """, (location,)).fetchone()

        # ── Top bait ─────────────────────────────────────────────────────────
        bait_rows = conn.execute("""
            SELECT bait, COUNT(*) AS entries,
                   SUM(caught) AS caught
            FROM fish_log WHERE location=? AND bait IS NOT NULL AND bait != ''
            GROUP BY bait ORDER BY entries DESC LIMIT 8
        """, (location,)).fetchall()

    pressure_order = ["Under 1010", "1010-1015", "1015-1020", "Over 1020"]

    return jsonify({
        "totals": {
            "entries": totals["entries"] or 0,
            "caught_trips": totals["caught_trips"] or 0,
            "total_fish": totals["total_fish"] or 0,
        },
        "species": [{"name": r["species"], "entries": r["entries"],
                     "total_fish": r["total_fish"] or r["entries"]} for r in species_rows],
        "monthly": [{"month": r["month"], "fish": r["fish"] or 0, "trips": r["trips"]} for r in monthly_rows],
        "solunar": {r["period"]: {"total": r["total"], "caught": r["caught"] or 0} for r in sol_rows},
        "pressure": {r["bucket"]: {"total": r["total"], "caught": r["caught"] or 0}
                     for r in pres_rows},
        "pressure_order": [p for p in pressure_order if any(r["bucket"] == p for r in pres_rows)],
        "bests": [{"species": r["species"], "max_size": r["max_size"],
                   "max_weight": r["max_weight"]} for r in bests_rows],
        "spots": [{"spot": r["spot"], "total": r["total"],
                   "caught": r["caught"] or 0, "fish": r["fish"] or 0} for r in spots_rows],
        "top_bait": [{"bait": r["bait"], "entries": r["entries"],
                      "caught": r["caught"] or 0} for r in bait_rows],
    })


@app.route("/api/records/<location>")
def api_records(location):
    if location not in LOCATION_NAMES:
        return jsonify({"error": "unknown location"}), 404
    tz = ZoneInfo("America/Chicago")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # Biggest by size
        size_rows = conn.execute("""
            SELECT f.species, f.size_in, f.weight_lbs, f.logged_at, f.notes, f.caught_location
            FROM fish_log f
            INNER JOIN (
                SELECT species, MAX(size_in) mx FROM fish_log
                WHERE location=? AND caught=1 AND size_in IS NOT NULL GROUP BY species
            ) b ON f.species=b.species AND f.size_in=b.mx AND f.location=?
            ORDER BY f.size_in DESC LIMIT 20
        """, (location, location)).fetchall()
        # Heaviest by weight (dedupe — skip if species already captured by size record)
        wt_rows = conn.execute("""
            SELECT f.species, f.size_in, f.weight_lbs, f.logged_at, f.notes, f.caught_location
            FROM fish_log f
            INNER JOIN (
                SELECT species, MAX(weight_lbs) mx FROM fish_log
                WHERE location=? AND caught=1 AND weight_lbs IS NOT NULL GROUP BY species
            ) b ON f.species=b.species AND f.weight_lbs=b.mx AND f.location=?
            ORDER BY f.weight_lbs DESC LIMIT 20
        """, (location, location)).fetchall()

    def fmt_row(r, key):
        return {
            "species": r["species"],
            "value": r[key],
            "size_in": r["size_in"],
            "weight_lbs": r["weight_lbs"],
            "date": datetime.fromtimestamp(r["logged_at"], tz=tz).strftime("%Y-%m-%d"),
            "spot": r["caught_location"],
        }

    return jsonify({
        "by_size":   [fmt_row(r, "size_in") for r in size_rows],
        "by_weight": [fmt_row(r, "weight_lbs") for r in wt_rows],
    })


# ── Tide chart endpoints ───────────────────────────────────────────────────────
@app.route("/api/tides/hourly")
def api_tides_hourly():
    """Hourly tide predictions for a full day — used to draw the tide curve chart."""
    station_id = request.args.get("id", "").strip()
    date_str   = request.args.get("date", datetime.now(tz=APP_TZ).strftime("%Y-%m-%d"))
    if not station_id:
        return jsonify({"error": "station id required"}), 400
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "invalid date"}), 400
    noaa_date = date_obj.strftime("%Y%m%d")
    try:
        r = requests.get(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
            params={
                "product":     "predictions",
                "station":     station_id,
                "datum":       "MLLW",
                "time_zone":   "lst_ldt",
                "interval":    "h",
                "units":       "english",
                "application": "fishing_dashboard",
                "format":      "json",
                "begin_date":  noaa_date,
                "end_date":    noaa_date,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return jsonify({"error": data["error"].get("message", "NOAA error")}), 400
        return jsonify({"predictions": data.get("predictions", [])})
    except Exception as exc:
        log.warning("NOAA hourly fetch failed for %s: %s", station_id, exc)
        return jsonify({"error": str(exc)}), 502


@app.route("/api/tides/week")
def api_tides_week():
    """7-day hi/lo tide forecast for a station starting from a given date."""
    station_id = request.args.get("id", "").strip()
    start_str  = request.args.get("date", datetime.now(tz=APP_TZ).strftime("%Y-%m-%d"))
    if not station_id:
        return jsonify({"error": "station id required"}), 400
    try:
        start_obj = datetime.strptime(start_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid date"}), 400

    dates = [start_obj + timedelta(days=i) for i in range(7)]

    def fetch_day(d):
        nd = d.strftime("%Y%m%d")
        try:
            r = requests.get(
                "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
                params={
                    "product": "predictions", "station": station_id,
                    "datum": "MLLW", "time_zone": "lst_ldt",
                    "interval": "hilo", "units": "english",
                    "application": "fishing_dashboard",
                    "format": "json", "begin_date": nd, "end_date": nd,
                },
                timeout=12,
            )
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                return d.isoformat(), []
            return d.isoformat(), data.get("predictions", [])
        except Exception:
            return d.isoformat(), []

    with ThreadPoolExecutor(max_workers=7) as ex:
        results = list(ex.map(fetch_day, dates))

    return jsonify({"week": {d: preds for d, preds in results}})


@app.route("/healthz")
def healthz():
    return "ok"

@app.template_filter("datetimeformat")
def datetimeformat(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=ZoneInfo("America/Chicago")).strftime("%m/%d %H:%M")
    except Exception:
        return "—"

if __name__ == "__main__":
    init_db()
    active_key = XAI_KEY if AI_PROVIDER == "xai" else GROQ_KEY
    active_var = "XAI_API_KEY" if AI_PROVIDER == "xai" else "GROQ_API_KEY"
    if not active_key:
        log.warning("%s not set — AI analysis disabled", active_var)
    elif ANALYSIS_AUTO:
        log.info("AI analysis provider: %s — background scheduler ON (every %dh, all locations)",
                 AI_PROVIDER, ANALYSIS_HOURS)
        threading.Thread(target=analysis_scheduler, daemon=True).start()
    else:
        log.info("AI analysis provider: %s — on-demand only (use the Analysis page button; "
                 "set ANALYSIS_AUTO=true to re-enable the background scheduler)", AI_PROVIDER)
    app.run(host="0.0.0.0", port=PORT)
