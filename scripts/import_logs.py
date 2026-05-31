#!/usr/bin/env python3
"""
Bulk-import handwritten fishing logs from a directory of scanned images.

Points at a folder of page images, sends each (or batches of pages) to the
fish-logger `/api/import/extract` endpoint — which uses Claude vision to
transcribe and structure every catch — then commits the results to the
`fish_log` database via `/api/import/commit` (with NOAA condition backfill).

It talks to the *running* fish-logger service over HTTP, so it reuses the exact
same extraction, species-mapping, and condition-backfill logic as the web UI.

Typical workflows
------------------
1) Review-then-commit (recommended for a big archive):

       # Pass 1 — extract everything to a JSON file, commit nothing:
       python3 scripts/import_logs.py -d ~/scans/freeport -l freeport_tx \\
           --dry-run --out proposed.json

       # ...open proposed.json, fix any misreads, delete junk rows...

       # Pass 2 — commit the reviewed file:
       python3 scripts/import_logs.py --commit-file proposed.json -l freeport_tx

2) One-shot (extract + commit in one go, no prompt):

       python3 scripts/import_logs.py -d ~/scans/freeport -l freeport_tx -y

Run with --help for all flags.
"""
import argparse
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs the 'requests' package:  pip install requests")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Server's IMPORT_MAX_IMAGES default is 20; keep batches at or under it.
MAX_BATCH = 20
COMMIT_CHUNK = 200          # entries per /api/import/commit request
RETRY_STATUS = {429, 500, 502, 503, 504}


def find_images(directory: Path, recursive: bool) -> list[Path]:
    walker = directory.rglob("*") if recursive else directory.glob("*")
    files = [p for p in walker if p.is_file() and p.suffix.lower() in IMG_EXTS]
    return sorted(files, key=lambda p: str(p).lower())


def media_type(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        return mt
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/jpeg")


def post_with_retry(session, url, *, retries, backoff, **kwargs):
    """POST with simple exponential backoff on transient/rate-limit statuses."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.post(url, timeout=180, **kwargs)
        except requests.RequestException as e:
            if attempt > retries:
                raise
            wait = backoff * (2 ** (attempt - 1))
            print(f"    network error ({e}); retry {attempt}/{retries} in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code == 402:        # permanent (auth/credit/model) — do not retry
            return resp
        if resp.status_code in RETRY_STATUS and attempt <= retries:
            wait = backoff * (2 ** (attempt - 1))
            print(f"    HTTP {resp.status_code}; retry {attempt}/{retries} in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        return resp


def extract_batch(session, host, location, model, batch: list[Path], args) -> dict:
    files = []
    handles = []
    try:
        for p in batch:
            fh = open(p, "rb")
            handles.append(fh)
            files.append(("images", (p.name, fh, media_type(p))))
        data = {"location": location}
        if model:
            data["model"] = model
        resp = post_with_retry(session, f"{host}/api/import/extract",
                               data=data, files=files,
                               retries=args.retries, backoff=args.sleep)
    finally:
        for fh in handles:
            fh.close()
    try:
        return resp.json()
    except ValueError:
        return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:300]}"}


def commit_entries(session, host, location, entries: list[dict], args) -> tuple[int, list]:
    total_inserted, all_errors = 0, []
    for i in range(0, len(entries), COMMIT_CHUNK):
        chunk = entries[i:i + COMMIT_CHUNK]
        resp = post_with_retry(session, f"{host}/api/import/commit",
                               json={"location": location, "entries": chunk},
                               retries=args.retries, backoff=args.sleep)
        try:
            data = resp.json()
        except ValueError:
            all_errors.append({"message": f"HTTP {resp.status_code}: {resp.text[:200]}"})
            continue
        if data.get("status") != "ok":
            all_errors.append({"message": data.get("message", "commit failed")})
            continue
        total_inserted += data.get("inserted", 0)
        all_errors.extend(data.get("errors", []))
    return total_inserted, all_errors


def summarize(entries: list[dict]) -> str:
    species = {}
    caught = sum(1 for e in entries if e.get("caught"))
    for e in entries:
        species[e.get("species", "?")] = species.get(e.get("species", "?"), 0) + 1
    top = sorted(species.items(), key=lambda kv: -kv[1])[:8]
    parts = ", ".join(f"{s}×{n}" for s, n in top)
    return f"{len(entries)} entries ({caught} caught) — {parts}" + (" …" if len(species) > 8 else "")


# ── extraction phase ───────────────────────────────────────────────────────────
def run_extract(args) -> list[dict]:
    directory = Path(args.dir).expanduser()
    if not directory.is_dir():
        sys.exit(f"Not a directory: {directory}")
    images = find_images(directory, args.recursive)
    if args.limit:
        images = images[:args.limit]
    if not images:
        sys.exit(f"No images ({', '.join(sorted(IMG_EXTS))}) found in {directory}"
                 + (" (try -r/--recursive)" if not args.recursive else ""))

    batch_size = max(1, min(args.batch, MAX_BATCH))
    batches = [images[i:i + batch_size] for i in range(0, len(images), batch_size)]
    print(f"Found {len(images)} image(s) in {directory} → {len(batches)} batch(es) "
          f"of up to {batch_size}, location='{args.location}'"
          + (f", model='{args.model}'" if args.model else "") + "\n")

    session = requests.Session()
    all_entries: list[dict] = []
    failures = 0
    for bi, batch in enumerate(batches, 1):
        names = ", ".join(p.name for p in batch)
        print(f"[{bi}/{len(batches)}] extracting: {names}")
        result = extract_batch(session, args.host, args.location, args.model, batch, args)
        if result.get("status") != "ok":
            msg = result.get("message", "unknown error")
            if result.get("fatal"):
                # bad key / no credits / no model access — every page would fail the
                # same way, so stop now rather than burning the rest of the archive.
                sys.exit(f"    ✗ FATAL: {msg}\nAborting — fix this and re-run.")
            failures += 1
            print(f"    ✗ {msg}", file=sys.stderr)
            continue
        entries = result.get("entries", []) or []
        src = batch[0].name if len(batch) == 1 else names
        for e in entries:
            e["_source"] = src
        if result.get("unreadable"):
            print(f"    ⚠ unreadable: {result['unreadable']}")
        print(f"    ✓ {summarize(entries)}")
        all_entries.extend(entries)
        if bi < len(batches) and args.sleep:
            time.sleep(args.sleep)

    print(f"\nExtraction done: {len(all_entries)} entries from "
          f"{len(batches) - failures}/{len(batches)} batches"
          + (f" ({failures} failed)" if failures else ""))
    return all_entries


def write_out(path: str, location: str, model: str | None, entries: list[dict]):
    payload = {
        "location": location,
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(entries),
        "entries": entries,
    }
    Path(path).write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(entries)} entries → {path}")


def load_commit_file(path: str, location_override: str | None) -> tuple[str, list[dict]]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):                       # bare list of entries
        entries, loc = data, location_override
    else:
        entries = data.get("entries", [])
        loc = location_override or data.get("location")
    if not loc:
        sys.exit("No location in file — pass -l/--location.")
    if not entries:
        sys.exit("No entries found in commit file.")
    return loc, entries


def main():
    ap = argparse.ArgumentParser(
        description="Bulk-import handwritten fishing logs into fish-logger.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("-d", "--dir", help="Directory of handwritten-log page images.")
    ap.add_argument("-l", "--location", help="Location key, e.g. freeport_tx, pensacola_fl.")
    ap.add_argument("--host", default="http://localhost:9879",
                    help="fish-logger base URL (default: %(default)s).")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="Recurse into sub-directories.")
    ap.add_argument("-b", "--batch", type=int, default=1,
                    help=f"Images per extract request, 1-{MAX_BATCH} (default 1). "
                         "Use >1 only when several scans are pages of ONE multi-page log.")
    ap.add_argument("--model", help="Override the server's IMPORT_MODEL for this run.")
    ap.add_argument("--min-confidence", type=float, default=0.0,
                    help="Drop entries below this confidence (0-1) before committing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Extract only; never write to the database (use with --out).")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="Commit without the interactive confirmation prompt.")
    ap.add_argument("--out", help="Write extracted entries to this JSON file for review.")
    ap.add_argument("--commit-file", help="Skip extraction; commit entries from this JSON file.")
    ap.add_argument("--limit", type=int, help="Only process the first N images (testing).")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="Seconds between API calls / retry backoff base (default 1.0).")
    ap.add_argument("--retries", type=int, default=3,
                    help="Retries on rate-limit / 5xx responses (default 3).")
    args = ap.parse_args()

    session = requests.Session()

    # ── commit-from-file mode ────────────────────────────────────────────────
    if args.commit_file:
        location, entries = load_commit_file(args.commit_file, args.location)
        before = len(entries)
        if args.min_confidence > 0:
            entries = [e for e in entries if (e.get("confidence") or 0) >= args.min_confidence]
        print(f"Loaded {before} entries from {args.commit_file}"
              + (f" ({before - len(entries)} below confidence {args.min_confidence} dropped)"
                 if before != len(entries) else "")
              + f"\n  → {summarize(entries)}")
        if not entries:
            sys.exit("Nothing to commit.")
        if not args.yes:
            if input(f"\nCommit {len(entries)} entries to '{location}'? [y/N] ").strip().lower() != "y":
                sys.exit("Aborted.")
        inserted, errors = commit_entries(session, args.host, location, entries, args)
        print(f"\n✓ Imported {inserted} entries to '{location}'."
              + (f"  {len(errors)} row(s) failed." if errors else ""))
        for e in errors[:10]:
            print(f"    ✗ {e}", file=sys.stderr)
        return

    # ── directory extract (+commit) mode ─────────────────────────────────────
    if not args.dir or not args.location:
        ap.error("directory mode needs both -d/--dir and -l/--location "
                 "(or use --commit-file to commit a reviewed JSON).")

    entries = run_extract(args)

    if args.min_confidence > 0:
        kept = [e for e in entries if (e.get("confidence") or 0) >= args.min_confidence]
        print(f"Confidence filter ≥{args.min_confidence}: kept {len(kept)}/{len(entries)}.")
        entries = kept

    if args.out:
        write_out(args.out, args.location, args.model, entries)

    if args.dry_run:
        print("\nDry run — nothing committed."
              + ("" if args.out else "  (tip: add --out FILE to save for review)"))
        return

    if not entries:
        sys.exit("No entries to commit.")

    print(f"\nReady to import: {summarize(entries)}")
    if not args.yes:
        if input(f"Commit {len(entries)} entries to '{args.location}'? [y/N] ").strip().lower() != "y":
            sys.exit("Aborted — re-run with --out to save, or --commit-file later.")

    inserted, errors = commit_entries(session, args.host, args.location, entries, args)
    print(f"\n✓ Imported {inserted} entries to '{args.location}'."
          + (f"  {len(errors)} row(s) failed." if errors else ""))
    for e in errors[:10]:
        print(f"    ✗ {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
