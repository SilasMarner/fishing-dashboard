#!/usr/bin/env python3
"""
qa_smoke.py — end-to-end QA smoke test for the Fishing Dashboard stack.

Dependency-light (Python stdlib only). Exercises every layer and exits non-zero
if anything fails, so it can run unattended (cron, CI, or the `fishing-qa` skill):

  • fishing_exporter   — :9877 Prometheus metrics + :9878 on-demand query API
  • Prometheus         — the fishing_tides scrape target is healthy/up
  • fish-logger pages  — /, /history, /analysis, /tides, /import, /map/salinity, /healthz
  • fish-logger APIs   — /api/recent, /api/conditions, /api/stations/search,
                         /api/tides/station, /api/maps, /api/weather (+ bad-input guards)
  • live data paths    — NOAA tide predictions, NGOFS2 salinity frames, NWS weather
  • Grafana            — live dashboard JSON still matches the repo backup

Usage:
  python3 scripts/qa_smoke.py
  python3 scripts/qa_smoke.py --host http://10.0.0.13:9879 --json
  python3 scripts/qa_smoke.py --skip-grafana            # app/exporter only
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# ── defaults (the homelab host; override on the CLI) ────────────────────────────
DEF_APP        = "http://10.0.0.13:9879"
DEF_EXPORTER   = "http://10.0.0.13:9877"
DEF_QUERY      = "http://10.0.0.13:9878"
DEF_PROM       = "http://10.0.0.13:9090"
DEF_GRAFANA    = "http://10.0.0.13:3000"
DEF_GF_USER    = "admin"
DEF_GF_PASS    = "changeme"
DASH_UID       = "fishing-tides-solunar-v1"
REPO_DASH      = os.path.join(os.path.dirname(__file__), "..",
                              "grafana", "fishing-tides-solunar-dashboard.json")

RESULTS = []  # (name, ok, detail)


def _req(url, *, auth=None, timeout=20):
    """GET url -> (status_code, body_bytes). Raises only on network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "fishing-qa-smoke"})
    if auth:
        import base64
        tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name, fn):
    """Run fn() -> (ok: bool, detail: str); record + print the result."""
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - any failure is a failed check
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    RESULTS.append((name, ok, detail))
    mark = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def expect_status(url, want=200, *, auth=None, contains=None, min_bytes=None):
    """Build a check fn asserting status (+ optional body contains / min size)."""
    def fn():
        code, body = _req(url, auth=auth)
        if code != want:
            return False, f"{url} -> HTTP {code} (wanted {want})"
        if contains and contains.encode() not in body:
            return False, f"{url} body missing {contains!r}"
        if min_bytes and len(body) < min_bytes:
            return False, f"{url} body only {len(body)}b (<{min_bytes})"
        return True, f"HTTP {code}, {len(body)}b"
    return fn


def main():
    ap = argparse.ArgumentParser(description="Fishing Dashboard QA smoke test")
    ap.add_argument("--host",     default=DEF_APP,      help="fish-logger base URL")
    ap.add_argument("--exporter", default=DEF_EXPORTER, help="exporter metrics base URL")
    ap.add_argument("--query",    default=DEF_QUERY,    help="exporter query API base URL")
    ap.add_argument("--prom",     default=DEF_PROM,     help="Prometheus base URL")
    ap.add_argument("--grafana",  default=DEF_GRAFANA,  help="Grafana base URL")
    ap.add_argument("--gf-user",  default=DEF_GF_USER)
    ap.add_argument("--gf-pass",  default=DEF_GF_PASS)
    ap.add_argument("--skip-grafana", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit JSON summary at the end")
    args = ap.parse_args()

    app   = args.host.rstrip("/")
    today = datetime.now().strftime("%Y-%m-%d")
    today_compact = datetime.now().strftime("%Y%m%d")

    print("== Exporter ==")
    check("exporter /metrics up",
          expect_status(f"{args.exporter}/metrics", contains="fishing_water_level_ft"))
    check("exporter query API",
          expect_status(f"{args.query}/query?location=freeport_tx&date={today_compact}",
                        contains="water_level_ft"))

    print("== Prometheus ==")
    def prom_target():
        code, body = _req(f"{args.prom}/api/v1/targets")
        if code != 200:
            return False, f"HTTP {code}"
        data = json.loads(body)
        for t in data.get("data", {}).get("activeTargets", []):
            if "9877" in t.get("scrapeUrl", ""):
                h = t.get("health")
                return h == "up", f"fishing target health={h}"
        return False, "no :9877 scrape target found"
    check("prometheus fishing target up", prom_target)

    print("== fish-logger pages ==")
    for path, needle in [("/", "freeport"), ("/history", None), ("/analysis", None),
                         ("/tides", None), ("/import", None), ("/map/salinity", None),
                         ("/embed", None)]:
        check(f"page {path}", expect_status(f"{app}{path}", min_bytes=500,
                                            contains=needle))
    check("page /healthz", expect_status(f"{app}/healthz", contains="ok"))

    print("== fish-logger APIs ==")
    check("api /api/recent", expect_status(f"{app}/api/recent", contains="["))
    check("api /api/conditions/freeport_tx",
          expect_status(f"{app}/api/conditions/freeport_tx", contains="fishing_score"))
    check("api /api/stations/search",
          expect_status(f"{app}/api/stations/search?q=corpus", contains="id"))
    check("api /api/tides/station (NOAA live)",
          expect_status(f"{app}/api/tides/station?id=8775421&date={today}",
                        contains="predictions"))

    def maps_check():
        code, body = _req(f"{app}/api/maps/8775421")
        if code != 200:
            return False, f"HTTP {code}"
        sal = json.loads(body).get("salinity", {})
        n = len(sal.get("frames", []))
        if not sal.get("in_coverage"):
            return False, "expected Gulf station in NGOFS2 coverage"
        return n > 0, f"{n} salinity frames"
    check("api /api/maps (NGOFS2 frames)", maps_check)

    check("api /api/weather (NWS live)",
          expect_status(f"{app}/api/weather?lat=28.95&lng=-95.36&date={today}",
                        contains="temp_f"))

    print("== bad-input guards ==")
    check("weather without coords -> 400",
          expect_status(f"{app}/api/weather", want=400))
    check("tides without id -> 400",
          expect_status(f"{app}/api/tides/station", want=400))
    check("maps unknown station -> 404",
          expect_status(f"{app}/api/maps/0000000", want=404))
    check("unknown page -> 404",
          expect_status(f"{app}/definitely-not-a-route", want=404))

    if not args.skip_grafana:
        print("== Grafana ==")
        def dash_sync():
            code, body = _req(f"{args.grafana}/api/dashboards/uid/{DASH_UID}",
                              auth=(args.gf_user, args.gf_pass))
            if code != 200:
                return False, f"dashboard fetch HTTP {code}"
            live = json.loads(body).get("dashboard", {})
            with open(REPO_DASH) as f:
                repo = json.load(f)
            repo = repo.get("dashboard", repo)
            lids = sorted(p.get("id") for p in live.get("panels", []))
            rids = sorted(p.get("id") for p in repo.get("panels", []))
            if lids != rids:
                only_live = set(lids) - set(rids)
                only_repo = set(rids) - set(lids)
                return False, f"panel drift live-only={only_live} repo-only={only_repo}"
            # panel 801 is the custom HTML panel — verify its options match
            def opts(d, pid):
                for p in d.get("panels", []):
                    if p.get("id") == pid:
                        return json.dumps(p.get("options", {}), sort_keys=True)
                return None
            if opts(live, 801) != opts(repo, 801):
                return False, "panel 801 options differ from repo backup"
            return True, f"{len(lids)} panels, panel 801 in sync"
        check("grafana dashboard matches repo backup", dash_sync)

    # ── summary ────────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'='*60}\n{passed}/{len(RESULTS)} checks passed"
          + (f", {len(failed)} FAILED" if failed else " — all green"))
    if failed:
        print("Failures:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")

    if args.json:
        print(json.dumps({
            "ts": int(time.time()),
            "passed": passed, "total": len(RESULTS),
            "failed": [{"name": n, "detail": d} for n, ok, d in RESULTS if not ok],
        }))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
