# Real Time River Discharge Forecasting and Landslide Early Warning System Using FastAPI and Groq LLM

Type an Indian town, district or farm location. The app reads the live 7-day river discharge, rainfall and soil moisture forecast for that spot, looks up the terrain height around it, and scores two risks from 0 to 100: **flood risk** and **landslide susceptibility**. Then an AI writes a 72-hour preparedness plan for your kind of place (a town ward, a farm, or a hill settlement). It is built for students, local planners and anyone who wants a clear picture of the week ahead.

This is a study project. It is **not** an official warning. Always follow IMD, CWC and your District Disaster Management Authority.

## What you need

- **Python 3.10 or newer.** Download it from https://www.python.org/downloads/ (on Windows, tick "Add python.exe to PATH").
- **A free Groq key** from https://console.groq.com/keys. It takes about a minute and needs no card.
- An internet connection. The forecast data comes from the free Open-Meteo service, which needs no key.

## Run it (one command)

- **macOS / Linux:** `chmod +x start.sh && ./start.sh`
- **Windows:** double-click `start.bat`

The first run creates a private Python environment and installs the requirements, so it takes a minute. The browser then opens at `http://127.0.0.1:8006`. Running the script again is quick.

## Add your Groq key

Click **API Keys** at the top right of the app and paste your key. It is saved only on your computer. You can also put it in the `.env` file as `GROQ_API_KEY=gsk_...` and restart.

The bundled samples open without a key. Your own locations need one.

## How to use it

1. Click a sample chip on the left, for example **Wayanad, Kerala**. A saved result opens instantly and uses none of your free runs.
2. Read the two gauges (flood risk and landslide susceptibility), the elevation, peak river discharge and 7-day rain, then hover the hydrograph to see each day.
3. Scroll down for the action cards: immediate 24-hour actions, farm and livestock protection, and an evacuation checklist.
4. To try your own place, type it in the box (for example `Kodagu, Karnataka`), pick an asset profile, and press **Run 7-day risk analysis**. Watch the four agents finish one by one.
5. **Basin vs. Basin Compare** puts two places side by side. **Emergency Action Brief** gives a one-page printable version of your latest result. **History** brings back earlier runs.

You get 15 live runs per hour. A run that fails (for example an unknown place or a busy AI service) is not counted.

## How it works

Your location goes through four agents in order:

1. **Agent 1: Geospatial & Elevation Resolver** (`app/geo.py`) finds the place with Open-Meteo Geocoding and reads a 3x3 grid of terrain heights (about 11 km across) from the Open-Meteo Elevation API to get elevation and local relief.
2. **Agent 2: River Discharge Hydrologist** (`app/geo.py`, `app/scoring.py`) reads the 7-day forecast and the past 92 days of river discharge in m³/s from the Open-Meteo Flood API, works out the peak surge ratio and a high-flow threshold, and scores flood risk.
3. **Agent 3: Terrain & Soil Saturation Assessor** (`app/geo.py`, `app/scoring.py`) reads rainfall and soil moisture from the Open-Meteo Forecast API and combines them with terrain relief into the landslide susceptibility score.
4. **Agent 4: Groq Disaster Preparedness Strategist** (`app/pipeline.py`, `app/ai_groq.py`) sends the measured numbers to Groq and gets back a structured 72-hour plan. If the main model is busy, it retries and then switches to the backup model.

`app/main.py` serves the pages and the routes, `app/db.py` keeps the 15-per-hour counter, history and your saved key in a small local SQLite file, and the screens are plain HTML, CSS and JavaScript in `app/static/`. The scores are calculated by fixed rules in `app/scoring.py`, so the AI never invents the numbers.

The files in `samples/results/` were made by `scripts/record_samples.py`. Run it again at any time to refresh them with today's forecast. With a Groq key set, the 72-hour plan in each file is written by Groq.

## Project structure

```
06_flood_landslide_early_warning/
├── start.sh                 one-command start for macOS / Linux
├── start.bat                one-command start for Windows
├── requirements.txt
├── .env.example             settings template (copied to .env on first run)
├── app/
│   ├── main.py              FastAPI app and all routes
│   ├── ai_groq.py           every Groq call: retries, fallback model, JSON checks
│   ├── pipeline.py          the four agents and the prompts
│   ├── geo.py               Open-Meteo geocoding, elevation, flood and forecast clients
│   ├── scoring.py           flood and landslide score rules
│   ├── db.py                SQLite storage (data/flood_risk.db, created on first run)
│   ├── keys.py              Groq key checks and masking
│   ├── access.py            signed-access check used by hosted copies
│   ├── config.py            reads .env
│   ├── errors.py            plain-sentence error type
│   └── static/
│       ├── index.html
│       ├── css/style.css
│       └── js/app.js
├── samples/
│   ├── wayanad-kerala.json
│   ├── kaziranga-assam.json
│   ├── hyderabad-telangana.json
│   └── results/             saved results for the three samples
└── scripts/
    └── record_samples.py    re-record the sample results
```

## Troubleshooting

- **"Port 8006 is already in use".** Another program is using it. Close it, or change `PORT=` in `.env` and run the start script again.
- **"The AI service is busy right now".** Groq's free tier is crowded. Wait a minute and press Run again. The app already retried and tried a backup model.
- **"Your Groq key was rejected".** Open **API Keys**, remove the old key and paste a fresh one from https://console.groq.com/keys.
- **"Python 3.10+ is needed" or "python not found".** Install Python from https://www.python.org/downloads/ and, on Windows, tick "Add python.exe to PATH". Then run the start script again.
- **The install failed or is very slow.** Check your internet connection and run the start script again. It carries on where it stopped.
- **"We couldn't find that place".** Try the district or a nearby larger town, and add the state, for example `Idukki, Kerala`.
- **"The live weather and river data service didn't answer".** Open-Meteo or your connection is down for a moment. Try again shortly.

Run the command:
Windows (PowerShell):
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8006

macOS / Linux (Terminal):
python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt && ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8006