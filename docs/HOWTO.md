# How-To Guide (for first-time self-hosters)

This walks through getting the Fishing Dashboard running on your own computer or server,
written for people who haven't done this kind of thing before. If you're already
comfortable with Docker and the command line, [`QUICKSTART.md`](../QUICKSTART.md) is faster.

No fishing knowledge needed either — this just tracks tides, weather, and your catches.

---

## What you're setting up

Four small programs that work together:

1. **The exporter** — checks government weather/tide websites every minute and remembers
   the numbers.
2. **A little database** (Prometheus) — stores those numbers over time.
3. **The web app** (fish-logger) — where you log the fish you caught, and where an AI
   writes you a fishing report based on your history.
4. **The dashboard** (Grafana) — the pretty screen that shows tide charts, weather, and
   your catch log all in one place.

All four run together with one command, using a free tool called **Docker**. You don't
need to understand how any of them work internally.

---

## Step 1 — Install Docker

Docker is free software that runs these four programs for you, in a self-contained way
that won't interfere with anything else on your computer.

- **Windows or Mac:** download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
  Open it once after installing — you should see a little whale icon appear.
- **Linux:** follow [Docker's install guide](https://docs.docker.com/engine/install/) for
  your distribution.

To check it worked, open a terminal (Windows: search for "PowerShell"; Mac: search for
"Terminal") and type:

```bash
docker --version
```

You should see a version number, not an error.

---

## Step 2 — Get the project files

**If you have `git` installed** (check with `git --version`):

```bash
git clone https://github.com/SilasMarner/fishing-dashboard.git
cd fishing-dashboard
```

**If you don't**, that's fine — download it as a ZIP instead:

1. Go to the [GitHub page](https://github.com/SilasMarner/fishing-dashboard)
2. Click the green **Code** button → **Download ZIP**
3. Unzip it somewhere you'll remember (e.g. your Desktop)
4. Open a terminal and navigate into that folder, e.g.:
   ```bash
   cd Desktop/fishing-dashboard-main
   ```

---

## Step 3 — Get a free AI key

The dashboard uses an AI to write you fishing reports (optional, but worth having). It's
free and takes two minutes:

1. Go to [console.groq.com](https://console.groq.com) and sign up (free, no credit card)
2. Once logged in, find **API Keys** in the sidebar and create one
3. Copy the key somewhere — it starts with `gsk_...`

---

## Step 4 — Configure your copy

In the project folder, make a copy of the example settings file:

```bash
cp .env.example .env
```

Open the new `.env` file in any text editor (Notepad, TextEdit, VS Code — whatever you
have) and fill in two things:

- `GROQ_API_KEY=` → paste the key from Step 3 after the `=`
- `GRAFANA_ADMIN_PASSWORD=` → make up a password (at least 8 characters) — this is what
  you'll use to log into the dashboard later

Everything else in that file can stay as-is for now. Save and close it.

---

## Step 5 — Start it up

Back in the terminal, still inside the project folder:

```bash
docker compose up -d
```

The first run takes a few minutes — it's downloading and building everything. You'll see
a lot of text scroll by; that's normal. When it's done, check everything started:

```bash
docker compose ps
```

You should see four services listed, all saying `Up` or `running`.

---

## Step 6 — Open the dashboard

Open a web browser and go to:

```
http://localhost:3000
```

(If you're setting this up on a different computer than the one you're browsing from,
replace `localhost` with that computer's IP address instead, e.g. `http://192.168.1.50:3000`.)

Log in with:
- Username: `admin`
- Password: whatever you set as `GRAFANA_ADMIN_PASSWORD` in Step 4

### Load the dashboard

The first time, you need to import the pre-built dashboard:

1. Click the **☰** menu (top-left) → **Dashboards**
2. Click **New** → **Import**
3. Click **Upload dashboard JSON file**
4. Select `grafana/fishing-tides-solunar-dashboard.json` from the project folder you downloaded
5. Click **Import**

You should now see tide charts, weather, and a catch-logging form for five sample fishing
spots (Texas and Florida, by default).

---

## Step 7 — Make it your own (optional)

The default setup tracks 5 real fishing spots as an example. To track your own spot
instead, see [`docs/ADDING_STATIONS.md`](ADDING_STATIONS.md) — it's more involved (editing
a couple of code files), so it's fine to skip this at first and just explore with the
sample locations.

---

## Logging your first catch

On the dashboard, scroll down to the **🐟 Log a Catch** panel. Pick a location, a species,
mark it Caught or Skunked, and hit the log button. The app automatically saves the tide
height, weather, and moon phase at that exact moment alongside your entry — you don't
have to fill any of that in yourself.

---

## Something not working?

- Run `docker compose ps` — anything not saying `Up`? Check its logs:
  `docker compose logs <service-name>`
- The full [Troubleshooting section in the README](../README.md#troubleshooting) covers
  the most common issues (blank tide chart, logging that never finishes, Grafana not
  finding the catch database).
- [`QUICKSTART.md`](../QUICKSTART.md) has the same steps in short form if you want a quick
  reference once you're comfortable with the basics.

## Turning it off / starting it again later

```bash
docker compose down       # stop everything (your data is kept)
docker compose up -d      # start it again later
```

Your catch log and settings are kept on disk between restarts — `docker compose down`
never deletes your data.
