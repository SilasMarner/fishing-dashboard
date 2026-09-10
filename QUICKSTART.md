# Quick Start

The whole stack — exporter, Flask app, Prometheus, Grafana — runs from one `docker compose up -d`. No systemd, no `sudo`, no manual directory setup.

## 1. Clone and configure

```bash
git clone https://github.com/SilasMarner/fishing-dashboard.git
cd fishing-dashboard
cp .env.example .env
# Edit .env — set GROQ_API_KEY and GRAFANA_ADMIN_PASSWORD at minimum
```

## 2. (Optional) Set your own locations

The default config tracks 5 real Texas/Florida fishing spots. To track your own, edit
`STATIONS` in `fishing_exporter/fishing_tide_exporter.py` and `SPECIES`/`LOCATION_NAMES`
in `fish_logger/app.py` — see [`docs/ADDING_STATIONS.md`](docs/ADDING_STATIONS.md).

## 3. Start the stack

```bash
docker compose up -d
docker compose ps                                  # all 4 containers should be "healthy"/"running"
curl -s http://localhost:9879/healthz               # fish-logger: "ok"
curl -s http://localhost:9877/metrics | grep fishing_score  # exporter is scraping
curl -s http://localhost:9090/-/ready                # prometheus: "Prometheus is Ready."
```

## 4. Import the Grafana dashboard

1. Open Grafana at `http://<your-host>:3000`
2. Log in with `admin` / the password you set in `.env`
3. **Dashboards → Import**, upload `grafana/fishing-tides-solunar-dashboard.json`
4. Pick the `prometheus` and `fish-sqlite` datasources when prompted (both are auto-provisioned)

That's it — no IP substitution needed, the dashboard resolves the fish-logger host from
your browser's URL automatically.

## Already running your own Prometheus/Grafana?

Use `docker-compose.prod.yml` instead, which only runs `fish-logger` + `fishing-exporter`
and attaches to your existing monitoring network:

```bash
MONITORING_NETWORK=your_existing_network_name docker compose -f docker-compose.prod.yml up -d
```

Then add a Prometheus scrape target for `fishing-exporter:9877` and import the dashboard
JSON into your existing Grafana.

## Next steps

- Full feature tour, API reference, and troubleshooting: [`README.md`](README.md)
- Adding a new fishing location: [`docs/ADDING_STATIONS.md`](docs/ADDING_STATIONS.md)
- Grafana plugin details: [`docs/GRAFANA_SETUP.md`](docs/GRAFANA_SETUP.md)
