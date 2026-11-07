# Weather-Aware Truck Routing

A React and Django app that compares three driving routes and recommends the one that best balances weather risk with travel time. The driver enters an origin, a destination, a departure time, and a load weight. The app samples weather along each road and draws the result on a map.

## Risk rules

Each checkpoint is scored from wind, rain, and snow at the truck's estimated arrival time. The checkpoint level is the **worst** of the three. A reading that lands on a boundary is placed in the **more severe** band.

Wind (mph), then load overrides:

| Wind | Up to 30,000 lb | Over 30,000 lb | Over 40,000 lb |
| --- | --- | --- | --- |
| Under 25 | Low | Low | Low |
| 25 to under 35 | Moderate | Moderate | Moderate |
| 35 to under 45 | High | High | Severe |
| 45 to under 55 | Severe | No travel | No travel |
| 55 or more | No travel | No travel | No travel |

The 40,000 lb wind rule replaces High with Severe. The 30,000 lb rule replaces Severe with No travel. A load over 40,000 lb also meets the 30,000 lb rule.

Rain (inches per hour): under 0.10 Low, under 0.25 Moderate, under 0.50 High, up to 1.00 Severe, above 1.00 No travel.

Snow (inches per hour): under 0.5 Low, under 1.0 Moderate, under 2.0 High, up to 3.0 Severe, above 3.0 No travel.

Scores used for the average: Low 1, Moderate 2, High 3, Severe 4, No travel 5.

Checkpoints are placed by **road miles**, including the start and the end. The interval is 10, 25, or 50 miles and is the same for every route. Each segment takes the level of the checkpoint at its end. Average risk is the mile-weighted mean. Miles with no weather reading are left out of the average.

Routes are ordered by:

1. Fewest No travel miles
2. Fewest Severe miles
3. Fewest High miles
4. Lowest average risk
5. Shortest travel time

No travel is ranked first because it is stricter than Severe. If every route still has no-travel miles, the app recommends the least bad one and says so.

Arrival time at a checkpoint is the departure time plus the route duration scaled by the fraction of road miles covered. The corridor heatmap is separate: it shows hours 0 through 48 from departure, and the slider only changes which preloaded hour is drawn.

## Routing and weather

- Origin and destination use Google Places autocomplete when `VITE_GOOGLE_MAPS_API_KEY` is set in `frontend/.env` (see `frontend/.env.example`). The picked coordinates are sent as `origin_place` / `destination_place`, so the server skips geocoding for them. The map shows A and B pins as soon as a place is picked.
- Without a Google key, or when the driver types a name without picking a suggestion, geocoding uses Nominatim. The server waits one second between the two lookups.
- Truck routes use [OpenRouteService](https://openrouteservice.org/) `driving-hgv` when `ORS_API_KEY` is set (free key at https://account.heigit.org/signup). ORS only computes alternatives for trips under 100 km, so longer trips take the main truck route and add two truck detours through points offset 25–45 miles to either side of it. Detours that are more than 1.85x slower or overlap an existing route are skipped.
- Without a key, or if ORS cannot produce three truck routes, the app uses the public OSRM car service, filled the same way. A banner explains that these are car routes.
- Weather provider: the free Open-Meteo API by default. Set `OPEN_METEO_API_KEY` to use Open-Meteo's paid API, or `OPEN_METEO_URL` to point at a self-hosted Open-Meteo server (see `backend/.env.example`). Other free weather APIs have lower limits for this workload, because every API charges per location.
- The heatmap is an even grid covering everything within 15 miles of any route, spaced so it uses at most `HEATMAP_CELL_BUDGET` (default 250) weather locations. Route checkpoints add about 40 more. The map draws each cell with a radius tied to the grid spacing, so cells overlap at every zoom.
- Open-Meteo's free tier counts each location as one call (about 600/minute, 5,000/hour, 10,000/day), and a plan needs a few hundred. Forecasts are therefore cached per grid cell for 30 minutes (`WEATHER_CACHE_SECONDS`), only the days the trip needs are requested, and a short-burst 429 is retried twice (after 2 s and 5 s). Re-planning the same or a nearby trip within 30 minutes uses no new calls. The cache is in-process memory; use Redis or Memcached if you run several workers.
- Weather comes from Open-Meteo. Wind is requested in mph. Rain and snow are requested in inches. If a response still labels snowfall as centimeters, those values are divided by 2.54.

## Run locally

Backend:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optional: set ORS_API_KEY
.venv/bin/python manage.py runserver 127.0.0.1:8010
```

Frontend, in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5174. The dev server proxies `/api` to Django on port 8010. If those ports are taken, pass a different port to `runserver` and match it in `frontend/vite.config.js`.

Tests:

```bash
cd backend
.venv/bin/python manage.py test planner.tests
```

The suite covers every band in the risk table, the load rules, ranking, sampling, weather parsing and unit conversion, routing fallback, the full plan pipeline (with network calls stubbed), and API validation. It needs no network access.

Departure must be between now and seven days ahead, which is inside the forecast window.

## Extending the rules

All risk logic is data in [backend/planner/services/risk.py](backend/planner/services/risk.py), bundled in a `RiskPolicy`:

- **Change a threshold:** edit the `Threshold` entries on `WIND`, `RAIN`, or `SNOW`.
- **Add a load rule:** append a `LoadRule(hazard, min_value, max_value, over_lb, level)` to `LOAD_RULES`. A rule can only raise a level, never lower it.
- **Change the recommendation order:** reorder or add `RankCriterion` entries in `RANKING`.
- **Add a hazard** (for example ice or visibility): add a `Hazard` to `HAZARDS` and a `WeatherVariable` with the same name to `VARIABLES` in [weather.py](backend/planner/services/weather.py). Checkpoints and heatmap cells pick it up automatically.
- **Use a different rule set per request or fleet:** build a `RiskPolicy(hazards=..., load_rules=..., ranking=...)` and pass it to `build_plan(..., policy=...)`.

See [test_policy.py](backend/planner/tests/test_policy.py) for working examples of each.

## API

`POST /api/plan/`

```json
{
  "origin": "Chicago, IL",
  "destination": "St. Louis, MO",
  "departure": "2026-11-05T13:00:00Z",
  "load_weight_lb": 35000,
  "sample_miles": 25,
  "origin_place": { "label": "Chicago, IL, USA", "lat": 41.8781, "lon": -87.6298 },
  "destination_place": null
}
```

`origin_place` and `destination_place` are optional.

`sample_miles` must be 10, 25, or 50. The response includes ranked routes, risk-colored segments, checkpoints, and `heatmap.points`. Each heatmap point carries 49 hours of risk, wind, rain, and snow. The map draws that corridor as circles underneath the routes, and the slider only changes the painted hour.
