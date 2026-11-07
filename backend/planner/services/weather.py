"""Batched, cached Open-Meteo forecasts.

Open-Meteo's free tier counts every location in a request as one call
(about 600/minute, 5,000/hour, 10,000/day), and one trip plan asks for a few
hundred locations. To stay under that:

* each grid cell's series is cached for ``WEATHER_CACHE_SECONDS`` (30 min)
  and reused while it still covers the hours the plan needs;
* only the forecast days the trip needs are requested, not all 16;
* a 429 caused by a short burst is retried once or twice before giving up.

Provider selection (backend/.env):

* ``OPEN_METEO_URL`` set   -> that server (for example a self-hosted Open-Meteo)
* ``OPEN_METEO_API_KEY`` set -> Open-Meteo's paid customer API
* neither                  -> the free public API
The key, when set, is sent as ``apikey`` to whichever server is used.

Wind is requested in mph. Rain and snow are requested in inches. If a
response still labels a value in another unit (for example snowfall in
centimeters), it is converted so the risk rules always see mph and in/hr.

``VARIABLES`` maps each risk hazard name to the Open-Meteo hourly field that
feeds it. To add a hazard, add a ``WeatherVariable`` here and a ``Hazard``
with the same name in ``risk.py``.
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings
from django.core.cache import cache

from .errors import WeatherError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CUSTOMER_FORECAST_URL = "https://customer-api.open-meteo.com/v1/forecast"
CHUNK_SIZE = 80
WORKERS = 4
MAX_FORECAST_DAYS = 16
CACHE_PREFIX = "openmeteo:v1"
RETRY_DELAYS_SECONDS = (2, 5)

WIND_TO_MPH = {"mp/h": 1, "mph": 1, "km/h": 0.621371, "m/s": 2.23694, "kn": 1.15078}
RAIN_TO_INCH = {"inch": 1, "mm": 1 / 25.4}
SNOW_TO_INCH = {"inch": 1, "cm": 1 / 2.54, "mm": 1 / 25.4}


@dataclass(frozen=True)
class WeatherVariable:
    name: str
    api_field: str
    unit_factors: dict


VARIABLES = (
    WeatherVariable("wind", "wind_speed_10m", WIND_TO_MPH),
    WeatherVariable("rain", "rain", RAIN_TO_INCH),
    WeatherVariable("snow", "snowfall", SNOW_TO_INCH),
)


class Forecast:
    """Hourly series per grid cell: ``{cell: {"times": [...], name: [...]}}``."""

    def __init__(self, series, variables=VARIABLES):
        self.series = series
        self.names = tuple(variable.name for variable in variables)

    def readings(self, lat, lon, when):
        """Mapping of variable name to the reading nearest ``when`` (or ``None``)."""
        hourly = self.series.get(cell_key(lat, lon))
        index = _nearest_index(hourly["times"], when) if hourly else None
        if index is None:
            return {name: None for name in self.names}
        return {name: hourly[name][index] for name in self.names}

    def at(self, lat, lon, when):
        """Backwards-compatible (wind, rain, snow) tuple."""
        values = self.readings(lat, lon, when)
        return values.get("wind"), values.get("rain"), values.get("snow")


def cell_key(lat, lon):
    return (round(float(lat), 2), round(float(lon), 2))


def fetch_forecasts(points, variables=VARIABLES, until=None, now=None):
    """Hourly forecasts for every grid cell touched by ``points``.

    ``until`` is the latest time the caller will read. Cached series that
    reach it are reused, and only enough forecast days to reach it are
    requested. ``None`` means the full 16-day window.
    """
    keys = list({cell_key(lat, lon) for lat, lon in points})
    if not keys:
        return Forecast({}, variables)
    now = now or datetime.now(timezone.utc)
    days = forecast_days_needed(until, now)
    covers = until or (_today(now) + timedelta(days=days) - timedelta(hours=1))

    names = tuple(variable.name for variable in variables)
    cache_keys = {key: _cache_key(key, variables) for key in keys}
    cached = cache.get_many(list(cache_keys.values()))
    series = {}
    missing = []
    for key in keys:
        entry = cached.get(cache_keys[key])
        if entry and all(name in entry for name in names) and entry["times"] and entry["times"][-1] >= covers:
            series[key] = entry
        else:
            missing.append(key)

    if missing:
        chunks = [missing[index : index + CHUNK_SIZE] for index in range(0, len(missing), CHUNK_SIZE)]
        workers = min(WORKERS, len(chunks))
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(lambda chunk: _fetch_chunk(chunk, variables, days), chunks))
        except WeatherError:
            raise
        except requests.RequestException as exc:
            raise WeatherError("The weather service could not be reached.") from exc
        fresh = {}
        for part in parts:
            fresh.update(part)
        series.update(fresh)
        cache.set_many(
            {cache_keys[key]: entry for key, entry in fresh.items()},
            timeout=getattr(settings, "WEATHER_CACHE_SECONDS", 1800),
        )

    if len(series) != len(keys):
        raise WeatherError("The weather service returned an incomplete forecast.")
    return Forecast(series, variables)


def forecast_days_needed(until, now):
    """Open-Meteo days (starting today 00:00 UTC) needed to reach ``until`` plus one spare hour."""
    if until is None:
        return MAX_FORECAST_DAYS
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    span = until.astimezone(timezone.utc) + timedelta(hours=1) - _today(now)
    days = math.ceil(span / timedelta(days=1))
    return max(1, min(MAX_FORECAST_DAYS, days))


def _today(now):
    now = now.astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _cache_key(key, variables):
    fields = ",".join(variable.api_field for variable in variables)
    return f"{CACHE_PREFIX}:{fields}:{key[0]:.2f}:{key[1]:.2f}"


def forecast_endpoint():
    """(url, api_key) for the configured Open-Meteo server."""
    url = (getattr(settings, "OPEN_METEO_URL", "") or "").strip()
    key = (getattr(settings, "OPEN_METEO_API_KEY", "") or "").strip()
    if not url:
        url = CUSTOMER_FORECAST_URL if key else FORECAST_URL
    return url, key


def _get_with_retry(params):
    """GET the forecast, retrying short-burst 429s. Long (hourly/daily) limits fail fast."""
    url, key = forecast_endpoint()
    if key:
        params = {**params, "apikey": key}
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        try:
            response = requests.get(url, params=params, timeout=40)
        except requests.RequestException as exc:
            raise WeatherError("The weather service could not be reached.") from exc
        if response.status_code != 429:
            return response
        reason = _reason(response).lower()
        if "hourly" in reason or "daily" in reason:
            raise WeatherError(
                "The free weather service limit for this hour or day is used up. "
                "Try again later, or re-plan a recent trip (cached forecasts are reused)."
            )
        if attempt < len(RETRY_DELAYS_SECONDS):
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
    raise WeatherError("The weather service is busy. Try again in a minute.")


def _reason(response):
    try:
        payload = response.json()
    except ValueError:
        return ""
    return str(payload.get("reason") or "") if isinstance(payload, dict) else ""


def _fetch_chunk(keys, variables=VARIABLES, days=MAX_FORECAST_DAYS):
    params = {
        "latitude": ",".join(str(lat) for lat, _lon in keys),
        "longitude": ",".join(str(lon) for _lat, lon in keys),
        "hourly": ",".join(variable.api_field for variable in variables),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "forecast_days": days,
    }
    response = _get_with_retry(params)
    if response.status_code >= 400:
        raise WeatherError("The weather service rejected the forecast request.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError("The weather service returned an unreadable forecast.") from exc
    rows = payload if isinstance(payload, list) else [payload]
    if len(rows) != len(keys):
        raise WeatherError("The weather service returned an unexpected forecast.")
    series = {}
    for key, row in zip(keys, rows):
        hourly = row.get("hourly") or {}
        units = row.get("hourly_units") or {}
        times = _parse_times(hourly.get("time") or [])
        entry = {"times": times}
        for variable in variables:
            entry[variable.name] = _scale(
                hourly.get(variable.api_field) or [],
                units.get(variable.api_field),
                variable.unit_factors,
            )
        width = len(times)
        if not width or any(len(entry[variable.name]) != width for variable in variables):
            raise WeatherError("The weather service returned an incomplete forecast.")
        series[key] = entry
    return series


def _parse_times(values):
    times = []
    for value in values:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise WeatherError("The weather service returned an unreadable time.") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        times.append(parsed.astimezone(timezone.utc))
    return times


def _scale(values, unit, factors):
    factor = factors.get(unit)
    if factor is None:
        raise WeatherError(f"The weather service used an unexpected unit ({unit}).")
    scaled = []
    for value in values:
        if value is None:
            scaled.append(None)
        else:
            scaled.append(float(value) * factor)
    return scaled


def _nearest_index(times, when):
    if not times:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    if when < times[0] - timedelta(hours=2) or when > times[-1] + timedelta(hours=2):
        return None
    if when <= times[0]:
        return 0
    if when >= times[-1]:
        return len(times) - 1
    low = 0
    high = len(times) - 1
    while low < high:
        mid = (low + high) // 2
        if times[mid] < when:
            low = mid + 1
        else:
            high = mid
    if low > 0 and (when - times[low - 1]) <= (times[low] - when):
        return low - 1
    return low
