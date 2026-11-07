import time

import requests

from .errors import GeocodeError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "WeatherAwareTruckRouting/1.0 (educational assessment)"}


def geocode(query):
    text = (query or "").strip()
    if not text:
        raise GeocodeError("Enter a place name.")
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": text, "format": "jsonv2", "limit": 1},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise GeocodeError("The place search service could not be reached.") from exc
    except ValueError as exc:
        raise GeocodeError("The place search service returned an unreadable response.") from exc
    if not payload:
        raise GeocodeError(f"Could not find “{text}”.")
    hit = payload[0]
    try:
        lat = float(hit["lat"])
        lon = float(hit["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodeError(f"Could not find “{text}”.") from exc
    return {"label": hit.get("display_name") or text, "lat": lat, "lon": lon}


def geocode_pair(origin, destination):
    """Look up both places, pausing so Nominatim stays under one request per second."""
    origin_hit = geocode(origin)
    time.sleep(1)
    destination_hit = geocode(destination)
    return origin_hit, destination_hit
