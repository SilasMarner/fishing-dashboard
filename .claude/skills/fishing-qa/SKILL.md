---
name: fishing-qa
description: Run a full autonomous QA pass on the Fishing Tides & Solunar dashboard stack (fish-logger Flask app, fishing_exporter, Prometheus, Grafana). Use when the user asks to "QA the fishing dashboard", "test the fish logger", "smoke-test the fishing stack", "check the fishing dashboard is healthy", or to validate the stack before/after a deploy. Runs scripts/qa_smoke.py, triages failures, applies fixes autonomously, rebuilds/redeploys, re-verifies, then commits and pushes to both origin and gitea.
version: 1.0.0
---

# Fishing Dashboard — autonomous QA pass

End-to-end health check of the whole fishing stack, fix anything broken, redeploy, re-verify, and report. The backbone is `scripts/qa_smoke.py` (stdlib-only, 22 checks, exits non-zero on any failure).

## Autonomous mode — run unattended, NEVER prompt

This skill is built to be launched and left alone. Run start-to-finish without stopping:

- **Never call `AskUserQuestion` and never pause for confirmation.** If a decision comes up, take the default documented here and note it in the final report.
- **Authorize your own changes.** If a check fails, diagnose it, fix it, rebuild/redeploy, re-run the smoke test, then commit and push automatically — no "should I?". The user has standing authorization for this skill to edit, rebuild, redeploy, commit, and push.
- **Commit + push to BOTH `origin` (GitHub) and `gitea`** — this repo has both remotes. Only commit when you actually changed files; a clean run with no findings ends with just the report.
- Run the **whole** smoke test even if something fails early — collect everything, fix in one batch, then one redeploy + re-verify pass.
- Produce a single final report at the end; don't stop for intermediate progress.

> Requires an auto-approving permission posture. If a run hits permission prompts, the user should launch it in auto-approve/bypass mode.

## Fixed environment (already set up — don't re-create)

| Thing | Value |
|------|------|
| Repo | `/root/git/fishing-dashboard` (remotes: `origin` GitHub + `gitea` http://10.0.0.54:3000) |
| App | `fishing-dashboard-fish-logger-1` **container**, host **http://10.0.0.13:9879** — run from this repo's `docker-compose.prod.yml`, attached to the shared `loki-promtail_monitoring` network. NOT part of Portainer stack 27 anymore (removed 2026-09-10). |
| Exporter | `fishing-dashboard-fishing-exporter-1` **container** (network alias `fishing-exporter`) → metrics :9877, query API :9878. The old host systemd service is retired/removed — don't reinstate it. |
| Prometheus | http://10.0.0.13:9090 (shared instance in Portainer stack 27; scrapes the exporter container by name `fishing-exporter:9877`, config at `/u01/docker/monitoring/config/prometheus/prometheus.yml`) |
| Grafana | http://10.0.0.13:3000, login `admin` / `changeme`, dashboard uid `fishing-tides-solunar-v1` |

## Step 1 — run the smoke test

```bash
cd /root/git/fishing-dashboard
python3 scripts/qa_smoke.py            # 22 checks; exit 0 = all green
```

It covers: exporter metrics + query API, the Prometheus scrape target, every fish-logger page (`/`, `/history`, `/analysis`, `/tides`, `/import`, `/map/salinity`, `/embed`, `/healthz`), every `/api/*` endpoint (incl. live NOAA tides, NGOFS2 salinity frames, NWS weather), bad-input guards (400/404), and that the **live Grafana dashboard JSON still matches the repo backup**. If it's all green and nothing else looks off, skip to Step 4.

## Step 2 — triage failures

Map a failed check to its cause:

- **exporter /metrics or query API down** → check `docker ps -f name=fishing-dashboard-fishing-exporter-1` and `docker logs fishing-dashboard-fishing-exporter-1 --tail 50`. Restart with `cd /root/git/fishing-dashboard && MONITORING_NETWORK=loki-promtail_monitoring docker compose -f docker-compose.prod.yml up -d fishing-exporter`. There is no host systemd fallback in normal operation — don't reinstate `fishing-exporter.service`.
- **prometheus target down** → exporter container down, or not reachable by name `fishing-exporter` on `loki-promtail_monitoring`. `docker ps`, `curl :9090/api/v1/targets`, `docker network inspect loki-promtail_monitoring`.
- **fish-logger page/api fails** → `docker logs fishing-dashboard-fish-logger-1 --tail 50`. Compare the running container to the repo: `docker exec fishing-dashboard-fish-logger-1 sha256sum /app/app.py` vs `sha256sum fish_logger/app.py`.
- **live data path fails** (tides/salinity/weather) → usually upstream (NOAA/NWS) flaking or a format change in the NGOFS2 option-file parser (`_salinity_frames` regex in `app.py`). Re-run once; if it's a real format change, fix the parser.
- **grafana dashboard drift** → the live dashboard was edited but the repo `grafana/fishing-tides-solunar-dashboard.json` backup wasn't updated (or vice-versa). Reconcile: pull the live JSON, diff, and update the repo backup to match (or push the repo version live), per the dashboard edit method in CLAUDE/memory.

## Step 3 — fix, redeploy, re-verify (only if something failed)

Apply fixes automatically. For **app.py / template / Dockerfile** changes, rebuild and redeploy via this repo's own `docker-compose.prod.yml` — **the repo's `.env` is what supplies `GROQ_API_KEY`/`AI_PROVIDER`/`OCR_PROVIDER`/`ANTHROPIC_API_KEY`**, so always run compose from `/root/git/fishing-dashboard` (it auto-loads `.env` from cwd):

```bash
cd /root/git/fishing-dashboard
docker build -t fish-logger:latest ./fish_logger
MONITORING_NETWORK=loki-promtail_monitoring FISHING_DATA_DIR=/u01/docker/monitoring/config/fishing \
  docker compose -f docker-compose.prod.yml up -d fish-logger --force-recreate
```

For **fishing_tide_exporter.py** changes, same pattern with the exporter service:

```bash
docker build -t fishing-exporter:latest ./fishing_exporter
MONITORING_NETWORK=loki-promtail_monitoring \
  docker compose -f docker-compose.prod.yml up -d fishing-exporter --force-recreate
```

After redeploy, confirm env survived: `docker exec fishing-dashboard-fish-logger-1 env | grep -E 'AI_PROVIDER|OCR_PROVIDER|GROQ_API_KEY'`, then **re-run `scripts/qa_smoke.py`** until it's green.

## Step 4 — commit, push, report

If files changed, commit with a clear message and **push to both remotes**:

```bash
git add -A && git commit -m "QA: <what was fixed>"
git push origin main && git push gitea main
```

A clean run with no findings changes nothing and just reports.

End with one report: per-section ✅/⚠️ (which of the 22 checks passed, what failed and why), what was fixed + the redeploy result, the final smoke-test result (`N/22 passed`), and the commit hash(es) pushed (or "no changes — clean run").

## Notes / gotchas

- The Grafana dashboard is stored in **Grafana's DB, not file-provisioned**; the repo JSON is a manually-synced backup. The smoke test's drift check is your guard against the two diverging.
- Since the exporter became a container (2026-09-10) on the same `monitoring`/`loki-promtail_monitoring` network, fish-logger reaches it at `http://fishing-exporter:9878` (`EXPORTER_QUERY_URL` env var) — this now works, unlike the old host-systemd topology where it couldn't.
- `gitea` remote token lives in the configured remote URL; `git push gitea main` just works from this repo.
