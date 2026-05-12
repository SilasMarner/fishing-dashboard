# Adding or Editing Fishing Stations

A "station" is a fishing location. Each location requires:
- A **NOAA tide station ID** — for tide predictions and water level
- **NWS grid coordinates** — for weather forecasts and observation data
- **Latitude / longitude** — for solunar and moon calculations

Stations are defined in **two files**. You must update both.

---

## File 1 — `fishing_exporter/fishing_tide_exporter.py`

This controls what data the exporter collects: tides, weather, solunar, fishing score.

Find the `STATIONS` dictionary near the top of the file (around line 25):

```python
STATIONS = {
    "freeport_tx": {
        "id":             "8772447",   # NOAA tide prediction station ID
        "water_level_id": "8771450",   # NOAA water level station ID (can differ from id)
        "name":           "Freeport TX",
        "lat":            28.9453,
        "lon":            -95.3597,
        "tz":             "America/Chicago",
        "nws_office":     "HGX",       # NWS forecast office code
        "nws_gridx":      63,          # NWS forecast grid X
        "nws_gridy":      59,          # NWS forecast grid Y
        "nws_station":    "KLBX",      # Nearest NWS observation station (ICAO code)
    },
    # ... more stations ...
}
```

Add a new entry using the same structure:

```python
    "my_new_location": {
        "id":             "XXXXXXX",
        "water_level_id": "XXXXXXX",
        "name":           "My Location TX",
        "lat":            29.1234,
        "lon":            -95.9876,
        "tz":             "America/Chicago",
        "nws_office":     "HGX",
        "nws_gridx":      50,
        "nws_gridy":      45,
        "nws_station":    "KXXX",
    },
```

---

## File 2 — `fish_logger/app.py`

This controls the web UI and AI analysis: species lists, display names.

### Add to `LOCATION_NAMES`

```python
LOCATION_NAMES = {
    "freeport_tx":      "Freeport TX",
    "padre_island_tx":  "N Padre Island TX",
    "pensacola_fl":     "Pensacola FL",
    "sargent_tx":       "Sargent TX",
    "matagorda_tx":     "Matagorda TX",
    "my_new_location":  "My Location TX",   # ← add this
}
```

### Add to `SPECIES`

```python
SPECIES = {
    # ... existing locations ...
    "my_new_location": [
        "Spotted Seatrout",
        "Red Drum (Redfish)",
        "Southern Flounder",
        # ... add species common to this location ...
        "Other",
    ],
}
```

The key in both dicts must match exactly what you used in `STATIONS`.

---

## How to Find the Values

### NOAA Tide Station ID

1. Go to [tidesandcurrents.noaa.gov](https://tidesandcurrents.noaa.gov/map/)
2. Click your location on the map
3. The station ID appears in the URL and info panel (e.g. `8772447`)
4. Verify it has tide prediction data:

```bash
curl "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&station=XXXXXXX&datum=MLLW&time_zone=lst_ldt&interval=hilo&units=english&format=json&begin_date=20250101&end_date=20250102"
```

Should return JSON with `predictions` array containing `t`, `v`, `type` fields.

### Water Level Station ID

Usually the same as the tide prediction ID. Some locations have separate water level recorders. Check:

```bash
curl "https://tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/XXXXXXX.json"
```

Look for `"greaterThanEqualMinDatum": true` or test the water level API directly:

```bash
curl "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_level&station=XXXXXXX&datum=MLLW&time_zone=lst_ldt&units=english&format=json&range=1"
```

### NWS Grid Coordinates and Office

```bash
curl "https://api.weather.gov/points/{lat},{lon}"
# Example:
curl "https://api.weather.gov/points/28.9453,-95.3597"
```

From the response JSON:
```json
{
  "properties": {
    "gridId": "HGX",      → nws_office
    "gridX": 63,          → nws_gridx
    "gridY": 59           → nws_gridy
  }
}
```

### Nearest NWS Observation Station (ICAO code)

```bash
curl "https://api.weather.gov/gridpoints/{office}/{gridx},{gridy}/stations"
# Example:
curl "https://api.weather.gov/gridpoints/HGX/63,59/stations"
```

The first station in the `features` array is the closest. Use its `stationIdentifier` (e.g. `KLBX`).

---

## After Adding a Station

### 1 — Restart the exporter

```bash
sudo systemctl restart fishing-exporter
# Verify the new location appears in metrics:
curl -s http://localhost:9877/metrics | grep my_new_location
```

Wait ~60 seconds for the first scrape cycle to complete.

### 2 — Rebuild and restart fish-logger

```bash
docker build -t fish-logger:latest ./fish_logger
docker compose up -d fish-logger
```

### 3 — Verify metrics in Prometheus

Open `http://<host>:9090/graph` and query:

```
fishing_score_now{location="my_new_location"}
```

### 4 — Add to Grafana

The Grafana tide panel (`gapit-htmlgraphics-panel`) has a `LOCS` array in its `onRender` JavaScript. Add the new location there:

```javascript
const LOCS = [
  { id:'freeport_tx',      label:'Freeport TX' },
  { id:'padre_island_tx',  label:'N Padre Island TX' },
  { id:'pensacola_fl',     label:'Pensacola FL' },
  { id:'sargent_tx',       label:'Sargent TX' },
  { id:'matagorda_tx',     label:'Matagorda TX' },
  { id:'my_new_location',  label:'My Location TX' },   // ← add this
];
```

Also add a button to the panel HTML:

```html
<button class="loc-btn" id="btn-my_new_location">My Location TX</button>
```

The `id` attribute must match `"btn-" + loc.id`.

---

## Existing Stations Reference

| Key | Name | NOAA Tide ID | Water Level ID | NWS Office | Grid X,Y | NWS Station |
|---|---|---|---|---|---|---|
| `freeport_tx` | Freeport TX | 8772447 | 8771450 | HGX | 63,59 | KLBX |
| `padre_island_tx` | N Padre Island TX | 8779770 | 8779770 | CRP | 116,59 | KCRP |
| `pensacola_fl` | Pensacola FL | 8729840 | 8729840 | MOB | 84,56 | KPNS |
| `sargent_tx` | Sargent TX | 8772985 | 8772985 | HGX | 53,51 | KBYY |
| `matagorda_tx` | Matagorda TX | 8773146 | 8773146 | HGX | 42,49 | KBYY |
