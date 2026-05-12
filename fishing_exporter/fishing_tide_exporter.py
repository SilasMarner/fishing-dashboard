#!/usr/bin/env python3
"""
Fishing & Tide Prometheus Exporter
- NOAA tide predictions + solunar fishing times
- NWS weather: temp, pressure trend, wind (km/h fix), precip chance, humidity, dewpoint
- Pressure: falls back through seaLevelPressure → barometricPressure → forecast grid
- On-demand /query endpoint (port 9878) for any date
- Three locations: Freeport TX, N Padre Island TX, Pensacola FL
Install deps:  pip install prometheus_client requests ephem
Run:           python3 fishing_tide_exporter.py
"""
import time
import json
import logging
import requests
import ephem
import threading
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import start_http_server, Gauge, Info

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fishing_exporter")

# ── Station config ─────────────────────────────────────────────────────────────
STATIONS = {
    "freeport_tx": {
        "id":             "8772447",
        "water_level_id": "8771450",
        "name":           "Freeport TX",
        "lat":            28.9453,
        "lon":            -95.3597,
        "tz":             "America/Chicago",
        "nws_office":     "HGX",
        "nws_gridx":      63,
        "nws_gridy":      59,
        "nws_station":    "KLBX",
    },
    "padre_island_tx": {
        "id":             "8779770",
        "water_level_id": "8779770",
        "name":           "N Padre Island TX",
        "lat":            27.5816,
        "lon":            -97.2322,
        "tz":             "America/Chicago",
        "nws_office":     "CRP",
        "nws_gridx":      116,
        "nws_gridy":      59,
        "nws_station":    "KCRP",
    },
    "pensacola_fl": {
        "id":             "8729840",
        "water_level_id": "8729840",
        "name":           "Pensacola FL",
        "lat":            30.4041,
        "lon":            -87.2111,
        "tz":             "America/Chicago",
        "nws_office":     "MOB",
        "nws_gridx":      84,
        "nws_gridy":      56,
        "nws_station":    "KPNS",
    },
    "sargent_tx": {
        "id":             "8772985",
        "water_level_id": "8772985",
        "name":           "Sargent TX",
        "lat":            28.7716,
        "lon":            -95.6168,
        "tz":             "America/Chicago",
        "nws_office":     "HGX",
        "nws_gridx":      53,
        "nws_gridy":      51,
        "nws_station":    "KBYY",
    },
    "matagorda_tx": {
        "id":             "8773146",
        "water_level_id": "8773146",
        "name":           "Matagorda TX",
        "lat":            28.7100,
        "lon":            -95.9139,
        "tz":             "America/Chicago",
        "nws_office":     "HGX",
        "nws_gridx":      42,
        "nws_gridy":      49,
        "nws_station":    "KBYY",
    },
}

NWS_HEADERS     = {"User-Agent": "FishingDashboard/1.0 (fishing-dashboard@localhost)"}
NOAA_API        = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
SCRAPE_INTERVAL = 600
EXPORTER_PORT   = 9877
QUERY_PORT      = 9878

# ── Prometheus metrics ─────────────────────────────────────────────────────────
tide_height         = Gauge("fishing_tide_height_ft",           "Predicted tide height ft",                   ["location","event_type","event_rank"])
tide_time_unix      = Gauge("fishing_tide_time_unix",           "Unix timestamp of tide event",               ["location","event_type","event_rank"])
water_level_ft      = Gauge("fishing_water_level_ft",           "Current observed water level ft",            ["location"])
solunar_major_start = Gauge("fishing_solunar_major_start_unix", "Start of major solunar period",              ["location","period"])
solunar_major_end   = Gauge("fishing_solunar_major_end_unix",   "End of major solunar period",                ["location","period"])
solunar_minor_start = Gauge("fishing_solunar_minor_start_unix", "Start of minor solunar period",              ["location","period"])
solunar_minor_end   = Gauge("fishing_solunar_minor_end_unix",   "End of minor solunar period",                ["location","period"])
moon_phase_pct      = Gauge("fishing_moon_phase_pct",           "Moon illumination 0-100",                    ["location"])
moon_phase_name     = Info("fishing_moon_phase",                "Moon phase name",                            ["location"])
sunrise_unix        = Gauge("fishing_sunrise_unix",             "Sunrise unix ts",                            ["location"])
sunset_unix         = Gauge("fishing_sunset_unix",              "Sunset unix ts",                             ["location"])
moonrise_unix       = Gauge("fishing_moonrise_unix",            "Moonrise unix ts",                           ["location"])
moonset_unix        = Gauge("fishing_moonset_unix",             "Moonset unix ts",                            ["location"])
fishing_score       = Gauge("fishing_score_now",                "Fishing quality score 0-10",                 ["location"])
fishing_score_label = Info("fishing_score_label",               "Fishing quality label",                      ["location"])

# Weather
wx_temp          = Gauge("fishing_weather_temp_f",             "Temperature F",                               ["location"])
wx_feels_like    = Gauge("fishing_weather_feels_like_f",       "Feels like F",                                ["location"])
wx_humidity      = Gauge("fishing_weather_humidity_pct",       "Relative humidity %",                         ["location"])
wx_pressure      = Gauge("fishing_weather_pressure_mb",        "Barometric pressure mb",                      ["location"])
wx_pressure_tend = Gauge("fishing_weather_pressure_trend",     "Pressure trend +1=rising 0=steady -1=falling",["location"])
wx_wind_speed    = Gauge("fishing_weather_wind_speed_mph",     "Wind speed mph",                              ["location"])
wx_wind_deg      = Gauge("fishing_weather_wind_deg",           "Wind direction degrees",                      ["location"])
wx_precip_chance = Gauge("fishing_weather_precip_chance_pct",  "Precipitation chance % next 6hr",             ["location"])
wx_cloud_cover   = Gauge("fishing_weather_cloud_cover_pct",    "Cloud cover %",                               ["location"])
wx_visibility    = Gauge("fishing_weather_visibility_miles",   "Visibility miles",                            ["location"])
wx_dewpoint      = Gauge("fishing_weather_dewpoint_f",         "Dewpoint F",                                  ["location"])
wx_info          = Info("fishing_weather",                     "Weather description and trend",               ["location"])

# Pressure history for trend calculation
_pressure_history: dict[str, list[float]] = {}

# ── Unit conversion helpers ────────────────────────────────────────────────────
def c_to_f(c):
    return round(c * 9/5 + 32, 1) if c is not None else None

def pa_to_mb(v):
    return round(v / 100, 2) if v is not None else None

def m_to_miles(v):
    return round(v / 1609.34, 1) if v is not None else None

def wind_to_mph(value, unit_code=None):
    """
    NWS windSpeed can be km/h or m/s depending on station.
    Check unitCode — default to km/h which is what most US ASOS stations send.
    """
    if value is None:
        return None
    if unit_code and ('m_s' in unit_code or 'm/s' in unit_code):
        return round(value * 2.23694, 1)
    # Default: km/h → mph
    return round(value * 0.621371, 1)

def nws_val(obj):
    """Extract numeric value from NWS quantity object {value, unitCode, qualityControl}."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get("value")
        return float(v) if v is not None else None
    try:
        return float(obj)
    except (TypeError, ValueError):
        return None

def nws_unit(obj):
    """Extract unitCode string from NWS quantity object."""
    if isinstance(obj, dict):
        return obj.get("unitCode", "")
    return ""

def deg_to_compass(deg):
    if deg is None:
        return "N/A"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(deg / 22.5) % 16]

def first_valid_pressure(obs):
    """
    Try seaLevelPressure first, then barometricPressure.
    Uses nws_val() to confirm the VALUE inside the object is not None —
    avoids the bug where the dict exists but contains a null value,
    causing plain 'or' chaining to return the null-valued object and
    never reach the fallback field.
    """
    for key in ("seaLevelPressure", "barometricPressure"):
        obj = obs.get(key)
        if obj is not None and nws_val(obj) is not None:
            log.info(f"  Pressure source: {key} = {obj}")
            return obj
    log.warning("  No valid pressure in observation — will try forecast grid fallback")
    return None

def fetch_forecast_pressure(office, gridx, gridy):
    """
    Fallback: pull pressure from the NWS gridpoint raw forecast product.
    Returns pressure in mb, or None if unavailable.
    The /gridpoints endpoint returns raw gridded values including pressure in Pa.
    """
    url = f"https://api.weather.gov/gridpoints/{office}/{gridx},{gridy}"
    try:
        r = requests.get(url, headers=NWS_HEADERS, timeout=15)
        r.raise_for_status()
        props = r.json().get("properties", {})

        # NWS uses different field names across offices
        for field in ("pressure", "seaLevelPressure", "barometricPressure"):
            block  = props.get(field, {})
            values = block.get("values", [])
            if values:
                raw_pa = values[0].get("value")
                if raw_pa is not None:
                    mb = pa_to_mb(float(raw_pa))
                    log.info(f"  Forecast grid pressure fallback: {field} = {raw_pa} Pa → {mb} mb")
                    return mb
    except Exception as e:
        log.warning(f"  Forecast grid pressure fallback failed: {e}")
    return None

# ── NWS weather ────────────────────────────────────────────────────────────────
def fetch_nws_observation(station_id: str) -> dict:
    url = f"https://api.weather.gov/stations/{station_id}/observations/latest"
    try:
        r = requests.get(url, headers=NWS_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("properties", {})
    except Exception as e:
        log.warning(f"NWS obs failed {station_id}: {e}")
        return {}

def fetch_nws_forecast(office, gridx, gridy) -> list:
    url = f"https://api.weather.gov/gridpoints/{office}/{gridx},{gridy}/forecast/hourly"
    try:
        r = requests.get(url, headers=NWS_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("properties", {}).get("periods", [])
    except Exception as e:
        log.warning(f"NWS forecast failed {office}/{gridx},{gridy}: {e}")
        return []

def compute_pressure_trend(location: str, current_mb: float) -> int:
    hist = _pressure_history.setdefault(location, [])
    hist.append(current_mb)
    if len(hist) > 6:
        hist.pop(0)
    if len(hist) < 3:
        return 0
    delta = hist[-1] - hist[0]
    if delta > 0.5:  return  1
    if delta < -0.5: return -1
    return 0

def fetch_weather(cfg: dict, label: str):
    obs      = fetch_nws_observation(cfg["nws_station"])
    forecast = fetch_nws_forecast(cfg["nws_office"], cfg["nws_gridx"], cfg["nws_gridy"])

    # ── Temperature / dewpoint / humidity ─────────────────────────────────────
    temp_c     = nws_val(obs.get("temperature"))
    dewpoint_c = nws_val(obs.get("dewpoint"))
    humidity   = nws_val(obs.get("relativeHumidity"))
    temp_f     = c_to_f(temp_c)
    dewpoint_f = c_to_f(dewpoint_c)

    # ── Pressure: observation with null-safe fallback to forecast grid ─────────
    pressure_obj = first_valid_pressure(obs)
    if pressure_obj is not None:
        pressure_mb = pa_to_mb(nws_val(pressure_obj))
    else:
        pressure_mb = fetch_forecast_pressure(
            cfg["nws_office"], cfg["nws_gridx"], cfg["nws_gridy"]
        )

    # ── Wind ──────────────────────────────────────────────────────────────────
    wind_obj  = obs.get("windSpeed")
    wind_raw  = nws_val(wind_obj)
    wind_unit = nws_unit(wind_obj)
    wind_dir  = nws_val(obs.get("windDirection"))
    wind_mph  = wind_to_mph(wind_raw, wind_unit)
    log.info(f"  {label} raw windSpeed: {wind_raw} [{wind_unit}] -> {wind_mph} mph")

    # ── Visibility ────────────────────────────────────────────────────────────
    vis_miles = m_to_miles(nws_val(obs.get("visibility")))

    # ── Description ───────────────────────────────────────────────────────────
    description = obs.get("textDescription", "")

    # ── Cloud cover from sky layers ───────────────────────────────────────────
    cloud_pct = 0
    okta_map  = {"FEW": 19, "SCT": 44, "BKN": 75, "OVC": 100, "VV": 100}
    for layer in obs.get("cloudLayers", []):
        cloud_pct = max(cloud_pct, okta_map.get(layer.get("amount", ""), 0))

    # ── Feels like: wind chill <=50F, heat index >=80F ───────────────────────
    feels_f = temp_f
    if temp_f is not None and wind_mph is not None:
        if temp_f <= 50 and wind_mph >= 3:
            feels_f = round(
                35.74 + 0.6215 * temp_f
                - 35.75 * (wind_mph ** 0.16)
                + 0.4275 * temp_f * (wind_mph ** 0.16), 1
            )
        elif temp_f >= 80 and humidity is not None:
            hi = (
                -42.379
                + 2.04901523  * temp_f
                + 10.14333127 * humidity
                - 0.22475541  * temp_f * humidity
                - 0.00683783  * temp_f ** 2
                - 0.05481717  * humidity ** 2
                + 0.00122874  * temp_f ** 2 * humidity
                + 0.00085282  * temp_f * humidity ** 2
                - 0.00000199  * temp_f ** 2 * humidity ** 2
            )
            feels_f = round(hi, 1)

    # ── Precip chance: max over next 6 forecast hours ─────────────────────────
    precip_chance = 0
    for period in forecast[:6]:
        pc = period.get("probabilityOfPrecipitation", {})
        if isinstance(pc, dict):
            v = pc.get("value")
            if v is not None:
                precip_chance = max(precip_chance, int(v))

    # ── Pressure trend ────────────────────────────────────────────────────────
    trend     = compute_pressure_trend(label, pressure_mb) if pressure_mb else 0
    trend_str = {1: "Rising ↑", 0: "Steady →", -1: "Falling ↓"}.get(trend, "Unknown")

    # ── Push to Prometheus ────────────────────────────────────────────────────
    if temp_f      is not None: wx_temp.labels(label).set(temp_f)
    if feels_f     is not None: wx_feels_like.labels(label).set(feels_f)
    if humidity    is not None: wx_humidity.labels(label).set(humidity)
    if pressure_mb is not None: wx_pressure.labels(label).set(pressure_mb)
    if wind_mph    is not None: wx_wind_speed.labels(label).set(wind_mph)
    if wind_dir    is not None: wx_wind_deg.labels(label).set(wind_dir)
    if vis_miles   is not None: wx_visibility.labels(label).set(vis_miles)
    if dewpoint_f  is not None: wx_dewpoint.labels(label).set(dewpoint_f)
    wx_pressure_tend.labels(label).set(trend)
    wx_precip_chance.labels(label).set(precip_chance)
    wx_cloud_cover.labels(label).set(cloud_pct)
    wx_info.labels(label).info({
        "description":    description or "Unknown",
        "pressure_trend": trend_str,
        "wind_dir":       deg_to_compass(wind_dir),
    })

    log.info(
        f"  {label} wx: {temp_f}F feels {feels_f}F, "
        f"{pressure_mb}mb ({trend_str}), "
        f"wind {wind_mph}mph {deg_to_compass(wind_dir)}, "
        f"precip {precip_chance}%, humidity {humidity}%, "
        f"clouds {cloud_pct}%, vis {vis_miles}mi"
    )

# ── NOAA helpers ───────────────────────────────────────────────────────────────
def fetch_noaa_hilo(station_id, tz, target_date=None):
    d = (target_date or date.today()).strftime("%Y%m%d")
    params = {
        "begin_date":  d, "end_date": d,
        "station":     station_id,
        "product":     "predictions",
        "datum":       "MLLW",
        "time_zone":   "lst_ldt",
        "interval":    "hilo",
        "units":       "english",
        "application": "fishing_dashboard",
        "format":      "json",
    }
    try:
        r = requests.get(NOAA_API, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("predictions", [])
    except Exception as e:
        log.warning(f"NOAA hilo failed {station_id}: {e}")
        return []

def fetch_noaa_water_level(station_id, target_date=None):
    target = target_date or date.today()
    if target == date.today():
        params = {
            "date":        "latest",
            "station":     station_id,
            "product":     "water_level",
            "datum":       "MLLW",
            "time_zone":   "lst_ldt",
            "units":       "english",
            "application": "fishing_dashboard",
            "format":      "json",
        }
        try:
            r = requests.get(NOAA_API, params=params, timeout=15)
            r.raise_for_status()
            readings = r.json().get("data", [])
            return float(readings[-1]["v"]) if readings else None
        except:
            return None
    else:
        d = target.strftime("%Y%m%d")
        params = {
            "begin_date":  d, "end_date": d,
            "station":     station_id,
            "product":     "predictions",
            "datum":       "MLLW",
            "time_zone":   "lst_ldt",
            "interval":    "h",
            "units":       "english",
            "application": "fishing_dashboard",
            "format":      "json",
        }
        try:
            r = requests.get(NOAA_API, params=params, timeout=15)
            r.raise_for_status()
            preds = r.json().get("predictions", [])
            noon  = [p for p in preds if "12:" in p.get("t", "")]
            if noon: return float(noon[0]["v"])
            return float(preds[len(preds)//2]["v"]) if preds else None
        except:
            return None

# ── Solunar ────────────────────────────────────────────────────────────────────
def get_moon_phase_name(illumination_pct):
    """Approximate phase name from illumination percentage."""
    p = illumination_pct
    if p <= 2:  return "New Moon"
    if p <= 48: return "Crescent"
    if p <= 52: return "Quarter Moon"
    if p <= 97: return "Gibbous"
    return "Full Moon"

def compute_solunar(lat, lon, local_tz, target_date=None):
    target = target_date or date.today()

    obs          = ephem.Observer()
    obs.lat      = str(lat)
    obs.lon      = str(lon)
    obs.date     = target.strftime("%Y/%m/%d")
    obs.pressure = 0

    moon = ephem.Moon()
    sun  = ephem.Sun()

    def to_unix(ep):
        return int(ephem.Date(ep).datetime().replace(tzinfo=ZoneInfo("UTC")).timestamp())

    def window(center_unix, half_seconds):
        return (center_unix - half_seconds, center_unix + half_seconds)

    # Moon rise / set
    try:    mr = obs.next_rising(moon)
    except: mr = None
    try:    ms = obs.next_setting(moon)
    except: ms = None

    # Upper transit (moon directly overhead)
    obs_upper          = ephem.Observer()
    obs_upper.lat      = obs.lat
    obs_upper.lon      = obs.lon
    obs_upper.date     = obs.date
    obs_upper.pressure = 0
    try:    upper = obs_upper.next_transit(moon)
    except: upper = None

    # Lower transit (moon underfoot) — ~12h 25min after upper transit.
    # ephem.Date units are days; 0.517 days = 12h 25min (the lunar half-period).
    # We seed a new observer at that offset and call next_transit to get the
    # precise crossing, guaranteeing it is a different event from upper.
    if upper is not None:
        obs_lower          = ephem.Observer()
        obs_lower.lat      = obs.lat
        obs_lower.lon      = obs.lon
        obs_lower.date     = ephem.Date(upper + 0.517)
        obs_lower.pressure = 0
        try:    lower = obs_lower.next_transit(moon)
        except: lower = None
    else:
        lower = None

    # Sun rise / set
    obs_sun          = ephem.Observer()
    obs_sun.lat      = obs.lat
    obs_sun.lon      = obs.lon
    obs_sun.date     = obs.date
    obs_sun.pressure = 0
    try:    sr = obs_sun.next_rising(sun)
    except: sr = None
    try:    ss = obs_sun.next_setting(sun)
    except: ss = None

    # Moon illumination for the target date
    moon.compute(target.strftime("%Y/%m/%d"))

    mr_unix    = to_unix(mr)    if mr    else 0
    ms_unix    = to_unix(ms)    if ms    else 0
    upper_unix = to_unix(upper) if upper else 0
    lower_unix = to_unix(lower) if lower else 0
    sr_unix    = to_unix(sr)    if sr    else 0
    ss_unix    = to_unix(ss)    if ss    else 0

    return {
        "moonrise_unix":     mr_unix,
        "moonset_unix":      ms_unix,
        "sunrise_unix":      sr_unix,
        "sunset_unix":       ss_unix,
        # Major periods: +/- 1 hour around upper and lower transit
        "major1":            window(upper_unix, 3600) if upper_unix else (0, 0),
        "major2":            window(lower_unix, 3600) if lower_unix else (0, 0),
        # Minor periods: +/- 30 min around moonrise and moonset
        "minor1":            window(mr_unix, 1800)    if mr_unix    else (0, 0),
        "minor2":            window(ms_unix, 1800)    if ms_unix    else (0, 0),
        "moon_illumination": moon.phase,
    }

def compute_fishing_score(sol, tides, target_date=None):
    target   = target_date or date.today()
    is_today = (target == date.today())
    now      = time.time() if is_today else datetime(
        target.year, target.month, target.day, 12, 0,
        tzinfo=ZoneInfo("America/Chicago")
    ).timestamp()

    score = 0.0

    # Moon phase contribution (max 3 pts)
    illum = sol.get("moon_illumination", 50)
    if illum >= 90 or illum <= 10:  score += 3.0   # Full or New moon
    elif 40 <= illum <= 60:         score += 1.0   # Quarter moon
    else:                           score += 1.5   # Everything else

    # Solunar proximity (capped at 6.5 before tide/dawn bonus)
    def period_score(start, end, max_pts):
        if not start: return 0
        if start <= now <= end:                        return max_pts
        if min(abs(now-start), abs(now-end)) < 1800:  return max_pts * 0.5
        return 0

    score += period_score(*sol["major1"], 3.0)
    score += period_score(*sol["major2"], 3.0)
    score += period_score(*sol["minor1"], 1.5)
    score += period_score(*sol["minor2"], 1.5)
    score  = min(score, 6.5)

    # Tide change proximity (max 2 pts)
    for t in tides:
        try:
            tu = int(
                datetime.strptime(t["t"], "%Y-%m-%d %H:%M")
                .replace(tzinfo=ZoneInfo("America/Chicago"))
                .timestamp()
            )
            d = abs(now - tu)
            if   d < 1800: score += 2.0; break
            elif d < 3600: score += 1.0; break
        except:
            pass

    # Dawn / dusk bonus (max 1.5 pts)
    for ev in [sol.get("sunrise_unix", 0), sol.get("sunset_unix", 0)]:
        if ev and abs(now - ev) < 3600:
            score += 1.5
            break

    score = min(round(score, 1), 10.0)
    label = (
        "EXCELLENT" if score >= 8 else
        "GREAT"     if score >= 6 else
        "GOOD"      if score >= 4 else
        "FAIR"      if score >= 2 else
        "SLOW"
    )
    return score, label

# ── On-demand query endpoint ───────────────────────────────────────────────────
def build_date_response(location, target_date):
    cfg = STATIONS.get(location)
    if not cfg:
        return {"error": f"Unknown location: {location}"}

    tides         = fetch_noaa_hilo(cfg["id"], cfg["tz"], target_date)
    wl            = fetch_noaa_water_level(cfg.get("water_level_id", cfg["id"]), target_date)
    sol           = compute_solunar(cfg["lat"], cfg["lon"], cfg["tz"], target_date)
    score, slabel = compute_fishing_score(sol, tides, target_date)

    highs = [t for t in tides if t.get("type", "").upper() == "H"]
    lows  = [t for t in tides if t.get("type", "").upper() == "L"]

    def fmt(events, etype):
        out = []
        for i, e in enumerate(events[:4], 1):
            try:
                tu = int(
                    datetime.strptime(e["t"], "%Y-%m-%d %H:%M")
                    .replace(tzinfo=ZoneInfo(cfg["tz"]))
                    .timestamp()
                )
                out.append({"rank": i, "type": etype, "time_unix": tu, "height_ft": float(e["v"])})
            except:
                pass
        return out

    return {
        "location":       location,
        "date":           target_date.strftime("%Y-%m-%d"),
        "water_level_ft": wl,
        "tides":          fmt(highs, "High") + fmt(lows, "Low"),
        "solunar": {
            "major1_start": sol["major1"][0], "major1_end": sol["major1"][1],
            "major2_start": sol["major2"][0], "major2_end": sol["major2"][1],
            "minor1_start": sol["minor1"][0], "minor1_end": sol["minor1"][1],
            "minor2_start": sol["minor2"][0], "minor2_end": sol["minor2"][1],
        },
        "moon": {
            "illumination": sol["moon_illumination"],
            "phase_name":   get_moon_phase_name(sol["moon_illumination"]),
        },
        "sun": {
            "sunrise_unix": sol["sunrise_unix"],
            "sunset_unix":  sol["sunset_unix"],
        },
        "moon_events": {
            "moonrise_unix": sol["moonrise_unix"],
            "moonset_unix":  sol["moonset_unix"],
        },
        "fishing": {"score": score, "label": slabel},
    }

class QueryHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(f"[query] {fmt % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/query":
            self.send_response(404); self.end_headers(); return

        params   = parse_qs(parsed.query)
        location = params.get("location", ["freeport_tx"])[0]
        date_str = params.get("date", [date.today().strftime("%Y%m%d")])[0]

        try:
            target = datetime.strptime(date_str, "%Y%m%d").date()
        except:
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"Invalid date. Use YYYYMMDD"}')
            return

        body = json.dumps(build_date_response(location, target)).encode()
        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def start_query_server():
    HTTPServer(("0.0.0.0", QUERY_PORT), QueryHandler).serve_forever()

# ── Main collection loop ───────────────────────────────────────────────────────
def collect():
    for key, cfg in STATIONS.items():
        log.info(f"Collecting {cfg['name']} ...")
        label = key

        # Tide predictions
        tides = fetch_noaa_hilo(cfg["id"], cfg["tz"])
        highs = [t for t in tides if t.get("type", "").upper() == "H"]
        lows  = [t for t in tides if t.get("type", "").upper() == "L"]

        for etype, events in [("High", highs), ("Low", lows)]:
            for rank, event in enumerate(events[:4], start=1):
                try:
                    ht = float(event["v"])
                    tu = int(
                        datetime.strptime(event["t"], "%Y-%m-%d %H:%M")
                        .replace(tzinfo=ZoneInfo(cfg["tz"]))
                        .timestamp()
                    )
                    tide_height.labels(label, etype, str(rank)).set(ht)
                    tide_time_unix.labels(label, etype, str(rank)).set(tu)
                except Exception as e:
                    log.warning(f"Tide parse error: {e}")

        # Current water level
        wl = fetch_noaa_water_level(cfg.get("water_level_id", cfg["id"]))
        if wl is not None:
            water_level_ft.labels(label).set(wl)

        # Solunar + celestial
        sol = compute_solunar(cfg["lat"], cfg["lon"], cfg["tz"])

        sunrise_unix.labels(label).set(sol["sunrise_unix"])
        sunset_unix.labels(label).set(sol["sunset_unix"])
        moonrise_unix.labels(label).set(sol["moonrise_unix"])
        moonset_unix.labels(label).set(sol["moonset_unix"])
        moon_phase_pct.labels(label).set(sol["moon_illumination"])
        moon_phase_name.labels(label).info({"phase": get_moon_phase_name(sol["moon_illumination"])})

        for pn in [1, 2]:
            solunar_major_start.labels(label, str(pn)).set(sol[f"major{pn}"][0])
            solunar_major_end.labels(label, str(pn)).set(sol[f"major{pn}"][1])
            solunar_minor_start.labels(label, str(pn)).set(sol[f"minor{pn}"][0])
            solunar_minor_end.labels(label, str(pn)).set(sol[f"minor{pn}"][1])

        # Fishing score
        score, slabel = compute_fishing_score(sol, tides)
        fishing_score.labels(label).set(score)
        fishing_score_label.labels(label).info({"quality": slabel})

        # Weather (includes null-safe pressure + forecast grid fallback)
        fetch_weather(cfg, label)

        log.info(f"  {cfg['name']}: score={score} ({slabel}), tides={len(tides)}")

def main():
    log.info(f"Starting Prometheus exporter on :{EXPORTER_PORT}")
    log.info(f"Starting on-demand query server  on :{QUERY_PORT}")
    start_http_server(EXPORTER_PORT)
    threading.Thread(target=start_query_server, daemon=True).start()
    log.info("Running. Ctrl-C to stop.")
    while True:
        try:
            collect()
        except Exception as e:
            log.error(f"Collection error: {e}", exc_info=True)
        log.info(f"Sleeping {SCRAPE_INTERVAL}s ...")
        time.sleep(SCRAPE_INTERVAL)

if __name__ == "__main__":
    main()
