#!/usr/bin/env python3
"""
Fish Logger — catch logging + AI analysis correlated with tide/weather/solunar data.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

# import anthropic          # ── Anthropic/Claude (commented out; see run_ai_analysis below)
# from google import genai  # ── Gemini (commented out; see run_ai_analysis below)
from openai import OpenAI
import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

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
PORT                 = int(os.environ.get("PORT", "9879"))
APP_TZ               = ZoneInfo("America/Chicago")
# ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")   # ── uncomment to use Claude instead
# GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "")      # ── uncomment to use Gemini instead

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

# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fish_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                location      TEXT NOT NULL,
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
    for label, metric in [("major", "fishing_solunar_major_start_unix"),
                           ("major", "fishing_solunar_major_end_unix")]:
        pass  # evaluated below
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


def get_conditions_for_date(location: str, target_dt: datetime) -> dict:
    """Return conditions for any datetime. Uses Prometheus for today, exporter query for other dates."""
    today = datetime.now(tz=APP_TZ).date()
    if target_dt.date() == today:
        return get_conditions(location)
    try:
        r = requests.get(
            f"{EXPORTER_QUERY_URL}/query",
            params={"location": location, "date": target_dt.strftime("%Y%m%d")},
            timeout=15,
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
        return get_conditions(location)

# ── AI analysis ────────────────────────────────────────────────────────────────
TREND_LABEL = {"-1.0": "falling", "-1": "falling", "0.0": "steady", "0": "steady",
               "1.0": "rising", "1": "rising"}

def run_ai_analysis(location: str) -> str:
    if AI_PROVIDER == "xai" and not XAI_KEY:
        return "AI analysis unavailable — XAI_API_KEY not set."
    if AI_PROVIDER != "xai" and not GROQ_KEY:
        return "AI analysis unavailable — GROQ_API_KEY not set."

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM fish_log WHERE location=? ORDER BY logged_at DESC LIMIT 300",
            (location,),
        ).fetchall()

    if not rows:
        return f"No fishing data logged yet for {LOCATION_NAMES.get(location, location)}."

    tz = ZoneInfo("America/Chicago")
    entries = []
    for r in rows:
        dt = datetime.fromtimestamp(r["logged_at"], tz=tz)
        trend_raw = str(r["pressure_trend"]) if r["pressure_trend"] is not None else None
        entries.append({
            "date":           dt.strftime("%Y-%m-%d %H:%M %a"),
            "species":        r["species"],
            "caught":         bool(r["caught"]),
            "count":          r["fish_count"],
            "size_in":        r["size_in"],
            "weight_lbs":     r["weight_lbs"],
            "notes":          r["notes"],
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

Analyze {len(entries)} fishing log entries and provide a thorough report with these sections:

## Conditions That Produce Catches
Which combinations of barometric pressure (and trend), tide stage/height, solunar period, temperature, and wind correlate most strongly with success vs. failure? Cite specific numbers where sample size allows (e.g. "8 of 10 catches occurred when pressure was rising above 1015 mb").

## Species Breakdown
For each species with enough data, note the conditions that produced catches, typical sizes/weights if logged, and any notable patterns.

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
        model  = "grok-3-mini"
    else:
        # Groq (default) — https://console.groq.com  (set GROQ_API_KEY in .env)
        client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")
        model  = "llama-3.3-70b-versatile"

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

def save_analysis(location: str, content: str,
                  model: str = "grok-3-mini" if os.environ.get("AI_PROVIDER","groq")=="xai" else "llama-3.3-70b-versatile"):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ai_analysis (location, analysis_type, content, model) VALUES (?,?,?,?)",
            (location, "historical", content, model),
        )
        conn.commit()

def analysis_scheduler():
    time.sleep(90)  # let stack stabilize before first run
    while True:
        for location in SPECIES:
            try:
                log.info("Running AI analysis for %s", location)
                content = run_ai_analysis(location)
                save_analysis(location, content)
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

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO fish_log (
                logged_at,
                location, species, caught, fish_count, size_in, weight_lbs, notes,
                tide_height_ft, tide_stage, water_level_ft,
                pressure_mb, pressure_trend, temp_f, wind_speed_mph, wind_deg,
                precip_chance, humidity, cloud_cover,
                solunar_period, moon_phase_pct, fishing_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(catch_dt.timestamp()),
            location, f.get("species"), 1 if f.get("caught") == "yes" else 0,
            count, size, weight, f.get("notes") or None,
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute(
            "SELECT * FROM ai_analysis WHERE location=? ORDER BY generated_at DESC LIMIT 1",
            (location,),
        ).fetchone()
    return render_template("analysis.html",
                           locations=LOCATION_NAMES,
                           selected=location,
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ai_analysis WHERE location=? ORDER BY generated_at DESC LIMIT 1",
            (location,),
        ).fetchone()
    if row:
        return jsonify({"status":"ok","content":row["content"],
                        "generated":row["generated_at"],"model":row["model"]})
    return jsonify({"status":"ok","content":None})

@app.route("/api/analyze/<location>", methods=["POST"])
def api_analyze(location):
    try:
        content = run_ai_analysis(location)
        save_analysis(location, content)
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO fish_log (
                logged_at,
                location, species, caught, fish_count, size_in, weight_lbs, notes,
                tide_height_ft, tide_stage, water_level_ft,
                pressure_mb, pressure_trend, temp_f, wind_speed_mph, wind_deg,
                precip_chance, humidity, cloud_cover,
                solunar_period, moon_phase_pct, fishing_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            int(catch_dt.timestamp()),
            location, f.get("species"), 1 if f.get("caught") == "yes" else 0,
            int(f.get("fish_count") or 1), size, weight, f.get("notes") or None,
            cond.get("tide_height_ft"), cond.get("tide_stage"), cond.get("water_level_ft"),
            cond.get("pressure_mb"), cond.get("pressure_trend"),
            cond.get("temp_f"), cond.get("wind_speed_mph"), cond.get("wind_deg"),
            cond.get("precip_chance"), cond.get("humidity"), cond.get("cloud_cover"),
            cond.get("solunar_period"), cond.get("moon_phase_pct"), cond.get("fishing_score"),
        ))
        conn.commit()
    return jsonify({"status": "ok"})

@app.route("/embed")
def embed():
    """Self-contained log form for Grafana iframe — no navbar, pure AJAX, no page navigation."""
    return render_template("embed.html", species=SPECIES, locations=LOCATION_NAMES)

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
    if active_key:
        log.info("AI analysis provider: %s", AI_PROVIDER)
        threading.Thread(target=analysis_scheduler, daemon=True).start()
    else:
        log.warning("%s not set — AI analysis disabled", active_var)
    app.run(host="0.0.0.0", port=PORT)
