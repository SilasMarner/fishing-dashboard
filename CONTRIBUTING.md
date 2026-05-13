# Contributing

Thanks for your interest in contributing to the Fishing Dashboard!

## Ways to Contribute

- **Bug reports** — open an issue using the Bug Report template
- **Feature requests** — open an issue using the Feature Request template
- **New locations** — see [`docs/ADDING_STATIONS.md`](docs/ADDING_STATIONS.md) for a step-by-step guide
- **Species lists** — additions welcome; edit `SPECIES` in `fish_logger/app.py`
- **Documentation** — typo fixes, clearer setup steps, new troubleshooting entries

## Development Setup

```bash
git clone https://github.com/SilasMarner/fishing-dashboard.git
cd fishing-dashboard
cp .env.example .env          # fill in GROQ_API_KEY and GRAFANA_ADMIN_PASSWORD
pip3 install -r fish_logger/requirements.txt
python3 fish_logger/app.py    # runs on port 9879
```

Seed the database with one week of test shark catches:

```bash
python3 scripts/seed_test_data.py
```

## Pull Request Guidelines

1. Fork the repo and create a branch from `main`
2. Keep changes focused — one fix or feature per PR
3. Test the golden path manually before opening a PR (log a catch, view history, trigger analysis)
4. Update the README if you change setup steps, env vars, or API endpoints
5. Fill out the pull request template fully

## Commit Style

Short imperative subject line, under 72 characters:

```
Add Galveston TX as a new location
Fix tide chart not loading on mobile
Update species list for Pensacola FL
```

## Reporting Security Issues

Please do **not** open a public issue for security vulnerabilities. See [`SECURITY.md`](SECURITY.md) instead.
