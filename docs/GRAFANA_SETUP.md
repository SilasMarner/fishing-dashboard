# Grafana Setup Guide

## Dashboard Overview

The **Fishing Tides & Solunar Dashboard** has these sections:

| Panel | Type | Description |
|---|---|---|
| Tides (top) | `gapit-htmlgraphics-panel` | Interactive tide chart with location switcher, date picker, weather strip, barometer, fishing score |
| Fish Log | iframe → `fish-logger:9879/embed` | In-Grafana catch logging form with bulk delete |
| Recent Catches | SQLite query | Table of last catches from fish_log.db |
| AI Analysis | iframe → `fish-logger:9879/analysis` | AI-generated pattern analysis |
| Per-location rows | Stat/gauge panels | Score, tide height, moon phase, solunar windows, sunrise/sunset for each location |

---

## Plugin Installation

### frser-sqlite-datasource

Automatically installed via the `GF_INSTALL_PLUGINS=frser-sqlite-datasource` environment variable in `docker-compose.yml`. No manual steps needed.

If it doesn't appear after startup, install manually:

```bash
docker exec grafana grafana cli plugins install frser-sqlite-datasource
docker restart grafana
```

### gapit-htmlgraphics-panel

Available in the Grafana plugin catalog. Install via:

```bash
docker exec grafana grafana cli plugins install gapit-htmlgraphics-panel
docker restart grafana
```

---

## Datasource: FishLog (SQLite)

The provisioning file at `grafana/provisioning/datasources/fish-sqlite.yaml` automatically creates the datasource on startup. The path `/fishing/fish_log.db` corresponds to the `FISHING_DATA_DIR` volume mounted at `/fishing` in the container.

**Verify it loaded:**

```bash
docker exec grafana sqlite3 /var/lib/grafana/grafana.db \
  "SELECT name, type FROM data_source WHERE type='frser-sqlite-datasource';"
```

---

## Datasource: Prometheus

The Prometheus datasource must be added manually or provisioned:

1. In Grafana: **Configuration → Data Sources → Add data source → Prometheus**
2. URL: `http://prometheus:9090`
3. Click **Save & Test**

Or provision it by creating `grafana/provisioning/datasources/prometheus.yaml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus
    access: proxy
    url: http://prometheus:9090
    editable: true
```

---

## Embedding fish-logger in Grafana

The log form is embedded as an iframe panel pointing to:

```
http://<host-ip>:9879/embed
```

**Important:** Use the host's LAN IP (e.g. `10.0.0.13`), not `localhost` — Grafana renders this URL in the user's browser, not inside Docker. The fish-logger container must be reachable from the browser.

The `/embed` page:
- Has no navigation bar (stripped down for iframe use)
- Submits catches via AJAX (`POST /api/log`) — no page navigation
- Shows a delete table at the bottom with single and bulk delete
- Sends `Access-Control-Allow-Origin: *` and `X-Frame-Options: ALLOWALL` headers

---

## Exporting the Dashboard JSON

To back up the dashboard or share it:

1. Open the dashboard in Grafana
2. Click the ⚙️ gear icon (Dashboard settings)
3. Go to **JSON Model**
4. Copy the JSON and save as `grafana/fishing-tides-solunar-dashboard.json`

To restore: **Dashboards → Import → Upload JSON file**.

---

## Admin Password Reset

If you lose access to Grafana:

```bash
docker exec grafana grafana cli admin reset-admin-password your_new_password
```

Note: `GF_SECURITY_ADMIN_PASSWORD` in the env only sets the password on **first run**. Use the CLI command above to change it after initial setup.
