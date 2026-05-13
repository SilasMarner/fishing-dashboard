# Fishing Dashboard

A self-hosted fishing intelligence stack that combines real-time NOAA tide predictions, NWS weather, solunar tables, and AI-powered catch analysis — all displayed in Grafana.

---

## Screenshots

### Catch Log Form — embedded in Grafana
![Embed Log Form](docs/screenshots/embed_log_form.png)

Location buttons, species dropdown (49+ species per Texas location, 59 for Pensacola including full shark variety), Caught/Skunked toggle, optional size/weight/notes fields, and a recent entries table with per-row and bulk-select delete. The full web UI (`/`) adds a datetime picker for backdating or planning ahead — see the Dashboard section below.

### Catch History — full conditions snapshot per entry

| Freeport TX | Padre Island TX |
|---|---|
| ![History Freeport](docs/screenshots/history_freeport_tx.png) | ![History Padre Island](docs/screenshots/history_padre_island_tx.png) |

| Pensacola FL | Sargent TX |
|---|---|
| ![History Pensacola](docs/screenshots/history_pensacola_fl.png) | ![History Sargent](docs/screenshots/history_sargent_tx.png) |

Every logged catch saves a full conditions snapshot at that moment: pressure + trend, tide height, solunar period, fishing score, wind, temperature, and more.

### Dashboard — Log Form by Location

| Freeport TX | Padre Island TX | Pensacola FL |
|---|---|---|
| ![Dashboard Freeport](docs/screenshots/dashboard_freeport_tx.png) | ![Dashboard Padre](docs/screenshots/dashboard_padre_island_tx.png) | ![Dashboard Pensacola](docs/screenshots/dashboard_pensacola_fl.png) |

### Dashboard — Date & Time Picker

| Now (live conditions) | Past date (historical) | Future date (forecast) |
|---|---|---|
| ![Datepicker Now](docs/screenshots/datepicker_now.png) | ![Datepicker Historical](docs/screenshots/datepicker_historical.png) | ![Datepicker Forecast](docs/screenshots/datepicker_forecast.png) |

Change the Date & Time field to any date and the conditions panel updates instantly — no page reload. Past dates pull NWS historical observations; future dates (≤7 days) pull NWS hourly forecast. The full conditions snapshot saved with the entry always matches the selected date and time.

### AI Analysis — Groq (Llama 3.3 70B)

Analysis runs automatically every 6 hours across all 5 locations. Correlates pressure trends, tide stages, solunar windows, wind, and temperature against catch history to produce a data-driven fishing report.

| Freeport TX | Pensacola FL |
|---|---|
| ![Analysis Freeport](docs/screenshots/analysis_freeport_tx.png) | ![Analysis Pensacola](docs/screenshots/analysis_pensacola_fl.png) |

| Padre Island TX | Sargent TX | Matagorda TX |
|---|---|---|
| ![Analysis Padre](docs/screenshots/analysis_padre_island_tx.png) | ![Analysis Sargent](docs/screenshots/analysis_sargent_tx.png) | ![Analysis Matagorda](docs/screenshots/analysis_matagorda_tx.png) |

### Grafana — Tide Chart and Dashboard
| Tide Panel | Full Dashboard |
|---|---|
| ![Grafana Tide Panel](docs/screenshots/grafana_tide_panel.png) | ![Grafana Dashboard](docs/screenshots/grafana_dashboard.png) |

Live tides, weather, solunar windows, fishing score gauge, moon phase, and tide events table — one collapsible row per location.

### Grafana — Date Navigation (Historical & Forecast)

| Past Date — NWS Historical Obs | Future Date — NWS Hourly Forecast |
|---|---|
| ![Grafana Past Date](docs/screenshots/grafana_past_date.png) | ![Grafana Future Date](docs/screenshots/grafana_future_date.png) |

Click the date arrows or picker in the tide panel to jump to any date. Past dates pull NWS historical observations (actual temp, pressure, wind, humidity, clouds); future dates (≤7 days) pull NWS hourly forecast. Weather, tide chart, solunar windows, and fishing score all update for the selected date — no page reload needed.

---

## Architecture

```
Host (systemd)
└── fishing_exporter          ← scrapes NOAA/NWS every 60 s, exposes Prometheus metrics
      ports: 9877 (metrics)
             9878 (on-demand date query for Grafana tide chart)

Docker (docker-compose)
├── prometheus                ← scrapes fishing_exporter:9877, stores 30 days
├── fish-logger               ← Flask app: log catches, AI analysis, Grafana embed page
│     port: 9879
└── grafana                   ← dashboard UI with tide chart, catch log embed, AI panel
      port: 3000
```

### Data flow

1. `fishing_exporter` fetches tides, weather, solunar, moon phase every 60 s per location and exposes them as Prometheus gauges.
2. Grafana's `gapit-htmlgraphics-panel` queries Prometheus (via `/api/datasources/proxy`) to render the interactive tide chart.
3. When you log a catch via the Grafana-embedded form, `fish-logger` simultaneously snapshots all current Prometheus metrics and stores them alongside the catch in `fish_log.db`.
4. The AI analysis scheduler runs every N hours, sends the last 300 catches + conditions to Claude, and saves the report to `fish_log.db`.
5. Grafana's `frser-sqlite-datasource` plugin queries `fish_log.db` directly for the catch history table.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker + Docker Compose | v2.x+ |
| Python 3.11+ | For the host-side exporter |
| `pip install prometheus_client requests ephem` | Exporter dependencies |
| Groq API key | For AI catch analysis (free) — get one at [console.groq.com](https://console.groq.com) |
| Grafana plugin `frser-sqlite-datasource` | Auto-installed via `GF_INSTALL_PLUGINS` env var |

---

## Quick Start

### 1 — Clone and configure

```bash
git clone https://github.com/SilasMarner/fishing-dashboard.git
cd fishing-dashboard
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and GRAFANA_ADMIN_PASSWORD at minimum
```

### 2 — Install and start the fishing exporter (host systemd service)

The exporter runs **on the Docker host** (not in a container) so it can reach external APIs without proxy complexity and expose metrics on a stable IP.

```bash
# Install Python dependencies
pip3 install prometheus_client requests ephem

# Copy exporter to a permanent location
sudo cp fishing_exporter/fishing_tide_exporter.py /opt/fishing_exporter/

# Install and enable the systemd service
sudo cp fishing_exporter/fishing_tide_exporter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fishing-exporter

# Verify it's running and exposing metrics
systemctl status fishing-exporter
curl -s http://localhost:9877/metrics | grep fishing_score
```

**The exporter exposes two ports:**
- `9877` — Prometheus metrics (scraped every 60 s by Prometheus)
- `9878` — On-demand date query endpoint used by the Grafana tide chart for historical dates

### 3 — Create data directories

```bash
mkdir -p data prometheus/data grafana/data grafana/provisioning/datasources
cp grafana/provisioning/datasources/fish-sqlite.yaml grafana/provisioning/datasources/
```

### 4 — Configure Prometheus to scrape the exporter

Edit `prometheus/prometheus.yml` and replace `host.docker.internal:9877` with your host's actual IP address if needed (e.g. `10.0.0.13:9877`).

### 5 — Start the Docker stack

```bash
docker compose up -d
```

Check that all three containers are healthy:

```bash
docker compose ps
curl -s http://localhost:9879/healthz          # fish-logger: should return "ok"
curl -s http://localhost:9090/-/ready          # prometheus: should return "Prometheus is Ready."
```

### 6 — Import the Grafana dashboard

The dashboard JSON is included at `grafana/fishing-tides-solunar-dashboard.json`. Two placeholders must be replaced before importing:

| Placeholder | Replace with |
|---|---|
| `YOUR_HOST_IP` | Your host's IP or hostname (e.g. `10.0.0.13`) |
| `YOUR_LOKI_DATASOURCE_UID` | Your Grafana Loki datasource UID (find it under **Connections → Data sources → Loki → Settings**, copy the UID from the URL) — only required if you use the Loki-backed Tides panel |

**Quick one-liner to patch and save a local copy:**

```bash
sed -e 's/YOUR_HOST_IP/10.0.0.13/g' \
    -e 's/YOUR_LOKI_DATASOURCE_UID/YOUR_ACTUAL_LOKI_UID/g' \
    grafana/fishing-tides-solunar-dashboard.json > /tmp/fishing-dashboard-import.json
```

Then import:

1. Open Grafana at `http://<your-host>:3000`
2. Log in with `admin` / the password you set in `.env`
3. Go to **Dashboards → Import**
4. Upload `/tmp/fishing-dashboard-import.json`

> **Datasources required:** `prometheus` (Prometheus), `fish-sqlite` (frser-sqlite-datasource — auto-provisioned from `grafana/provisioning/datasources/fish-sqlite.yaml`), and optionally a Loki datasource for the tide chart panel.

---

## Fish Logger — Web Interface

The `fish-logger` app runs on port **9879** and has two interfaces:

| URL | Purpose |
|---|---|
| `http://<host>:9879/` | Full web UI — log catches, view history, AI analysis |
| `http://<host>:9879/embed` | Stripped-down iframe version embedded in Grafana |
| `http://<host>:9879/analysis?location=freeport_tx` | AI analysis page |

### Logging a catch

1. Select your location tab (Freeport TX, N Padre Island TX, Pensacola FL, Sargent TX, Matagorda TX)
2. Choose species from the location-specific dropdown
3. Mark Caught or Skunked, fill in count/size/weight/notes (all optional except species)
4. Set the **Date & Time** — defaults to now, but you can change it to backdate a catch or pre-log a planned trip
5. Click **Log Entry + Snapshot Conditions**

When you log a catch, the app snapshots the full conditions for the selected date and time: tide height, water level, barometric pressure + trend, temperature, wind speed, precipitation chance, humidity, cloud cover, solunar period, moon phase, and fishing score. All are saved alongside your catch entry.

**How conditions are sourced by date:**

| Date | Source |
|---|---|
| Today | Live Prometheus metrics from the fishing exporter |
| Past date | NWS historical observations (reading nearest to noon for that day) |
| Future date (≤7 days) | NWS hourly forecast (period nearest to noon for that day) |

The conditions panel on the right updates live as you change the date picker — no page reload needed. Note that past dates won't have a real-time tide height (only the nearest Hi/Lo predictions are stored), and future dates won't have barometric pressure (not available in NWS forecasts).

### Deleting entries

- **Single:** Click the 🗑 button on the row
- **Multiple:** Check individual rows (or use **Select All**), then click **Delete Selected**

### AI Analysis

The AI scheduler runs automatically every 6 hours (configurable via `ANALYSIS_INTERVAL_HOURS`). To trigger it manually:

```bash
curl -X POST http://localhost:9879/api/analyze/freeport_tx
```

Or click **Run Now** in the Analysis tab of the web UI.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/recent` | Last 20 log entries as JSON |
| `POST` | `/api/log` | Log a catch (form data: location, species, caught, fish_count, size_in, weight_lbs, notes) |
| `POST` | `/api/log/<id>` | Delete a single entry (also accepts DELETE) |
| `POST` | `/api/log/bulk-delete` | Delete multiple entries: `{"ids": [1, 2, 3]}` |
| `GET` | `/api/analysis/<location>` | Latest AI analysis for a location |
| `POST` | `/api/analyze/<location>` | Trigger immediate AI analysis |
| `GET` | `/api/conditions/<location>` | Current Prometheus conditions snapshot |

---

## Locations and Species

Locations are defined in two separate files:

### fishing_exporter — tide, weather, solunar data

**File:** `fishing_exporter/fishing_tide_exporter.py`  
**Dictionary:** `STATIONS` (top of file, ~line 25)

### fish_logger — species lists, location names

**File:** `fish_logger/app.py`  
**Dictionaries:** `SPECIES` and `LOCATION_NAMES` (top of file, ~line 30)

See [`docs/ADDING_STATIONS.md`](docs/ADDING_STATIONS.md) for step-by-step instructions on adding a new location.

---

## Grafana Plugin Requirements

The dashboard uses two Grafana plugins:

| Plugin | ID | Purpose |
|---|---|---|
| HTML Graphics | `gapit-htmlgraphics-panel` | Tide chart with interactive location switcher |
| SQLite | `frser-sqlite-datasource` | Direct query of fish_log.db for catch history |

`frser-sqlite-datasource` is installed automatically via the `GF_INSTALL_PLUGINS` env var in `docker-compose.yml`.

`gapit-htmlgraphics-panel` must be installed manually if you're on Grafana Cloud, or is available in the plugin catalog for self-hosted Grafana.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes (for AI) | — | Groq API key — free at [console.groq.com](https://console.groq.com) |
| `XAI_API_KEY` | No | — | xAI/Grok API key (alternative) — [console.x.ai](https://console.x.ai) |
| `AI_PROVIDER` | No | `groq` | AI provider: `groq` or `xai` |
| `GRAFANA_ADMIN_PASSWORD` | Yes | — | Grafana admin password (≥8 chars) |
| `GRAFANA_ADMIN_USER` | No | `admin` | Grafana admin username |
| `FISHING_DATA_DIR` | No | `./data` | Directory for fish_log.db |
| `PROMETHEUS_CONFIG_DIR` | No | `./prometheus` | Prometheus config directory |
| `PROMETHEUS_DATA_DIR` | No | `./prometheus/data` | Prometheus TSDB storage |
| `GRAFANA_DATA_DIR` | No | `./grafana/data` | Grafana persistent storage |
| `GRAFANA_PROVISIONING_DIR` | No | `./grafana/provisioning` | Grafana provisioning configs |
| `ANALYSIS_INTERVAL_HOURS` | No | `6` | AI analysis re-run interval |
| `DB_PATH` | No | `/data/fish_log.db` | Path inside fish-logger container |
| `PROMETHEUS_URL` | No | `http://prometheus:9090` | Prometheus URL seen by fish-logger |
| `EXPORTER_QUERY_URL` | No | `http://localhost:9878` | Exporter on-demand query endpoint for historical/forecast conditions |
| `PORT` | No | `9879` | fish-logger HTTP port |

---

## Troubleshooting

### fish-logger says "Logging…" and never responds

The app queries Prometheus for 16 metrics at log time (2 s timeout each). If Prometheus is unreachable, logging times out. Check:

```bash
docker exec fish-logger curl -s http://prometheus:9090/-/ready
docker logs fish-logger --tail 30
```

Make sure both containers are on the same Docker network (`monitoring`).

### No metrics in Prometheus / tide chart blank

Check the exporter is running and Prometheus can reach it:

```bash
systemctl status fishing-exporter
journalctl -u fishing-exporter -n 30
curl -s http://localhost:9877/metrics | grep fishing_score_now
# Then check Prometheus targets:
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -A5 fishing
```

### Grafana can't find fish_log.db

The `FISHING_DATA_DIR` volume must be mounted at `/fishing` inside the Grafana container AND the SQLite datasource path must be `/fishing/fish_log.db`. Verify with:

```bash
docker exec grafana ls /fishing/
```

### AI analysis not generating

```bash
docker logs fish-logger | grep -i "groq\|analysis\|error"
curl -s http://localhost:9879/api/conditions/freeport_tx  # verify app is reachable
```

Make sure `GROQ_API_KEY` (or `XAI_API_KEY` if using `AI_PROVIDER=xai`) is set and valid. The scheduler waits 90 s after startup before the first run.
