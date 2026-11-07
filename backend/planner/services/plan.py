"""Turn a trip request into scored routes and a preloaded corridor forecast."""

from datetime import timedelta

from django.conf import settings

from .geocode import geocode, geocode_pair
from .risk import DEFAULT_POLICY, SCORES, segment_stats
from .routing import fetch_routes
from .sample import corridor_grid, sample_checkpoints, subline_coordinates
from .weather import cell_key, fetch_forecasts

HEATMAP_HOURS = 49
# Each heatmap cell is one weather location (one Open-Meteo "call").
# Route checkpoints need only ~40, so this budget is most of the cost of a plan.
DEFAULT_HEATMAP_CELL_BUDGET = 250


def build_plan(
    origin,
    destination,
    departure,
    load_lb,
    sample_miles,
    api_key,
    policy=DEFAULT_POLICY,
    origin_place=None,
    destination_place=None,
):
    """``origin_place`` / ``destination_place`` are optional ``{label, lat, lon}``
    dicts (for example from Google Places autocomplete). When given, that end
    is not geocoded again.
    """
    origin_hit, destination_hit = _resolve_places(origin, destination, origin_place, destination_place)
    provider, raw_routes, warning = fetch_routes(origin_hit, destination_hit, api_key)

    prepared = []
    for raw in raw_routes:
        checkpoints, cumulative = sample_checkpoints(raw["coordinates"], sample_miles)
        prepared.append(
            {
                "coordinates": raw["coordinates"],
                "duration_seconds": raw["duration_seconds"],
                "checkpoints": checkpoints,
                "cumulative": cumulative,
            }
        )

    budget = getattr(settings, "HEATMAP_CELL_BUDGET", DEFAULT_HEATMAP_CELL_BUDGET)
    grid, heat_spacing = corridor_grid([route["coordinates"] for route in prepared], budget)
    heat_cells = list(dict.fromkeys(cell_key(lat, lon) for lat, lon in grid))

    weather_points = [(point["lat"], point["lon"]) for route in prepared for point in route["checkpoints"]]
    weather_points.extend(heat_cells)
    # Latest hour anyone reads: the end of the slowest route or the last heatmap hour.
    longest = max((route["duration_seconds"] for route in prepared), default=0)
    until = departure + max(timedelta(seconds=longest), timedelta(hours=HEATMAP_HOURS - 1))
    forecast = fetch_forecasts(weather_points, until=until)

    routes = []
    for route in prepared:
        total_miles = route["checkpoints"][-1]["mile"] if route["checkpoints"] else 0.0
        duration = route["duration_seconds"]
        checkpoints = []
        for point in route["checkpoints"]:
            fraction = 0 if total_miles <= 0 else point["mile"] / total_miles
            eta = departure + timedelta(seconds=duration * fraction)
            readings = forecast.readings(point["lat"], point["lon"], eta)
            level, drivers = policy.classify(readings, load_lb)
            checkpoint = {
                "lat": round(point["lat"], 5),
                "lon": round(point["lon"], 5),
                "mile": round(point["mile"], 3),
                "eta": eta.isoformat(timespec="seconds"),
            }
            for hazard in policy.hazards:
                checkpoint[hazard.field] = _round(readings.get(hazard.name), hazard.digits)
            checkpoint["level"] = level
            checkpoint["drivers"] = drivers
            checkpoints.append(checkpoint)
        segments = []
        for start, end in zip(checkpoints, checkpoints[1:]):
            segments.append(
                {
                    "level": end["level"] or "unknown",
                    "coordinates": subline_coordinates(
                        route["coordinates"],
                        route["cumulative"],
                        start["mile"],
                        end["mile"],
                    ),
                }
            )
        geometry = [
            [round(lon, 5), round(lat, 5)] for lat, lon in route["coordinates"]
        ]
        routes.append(
            {
                "distance_miles": round(total_miles, 1),
                "duration_seconds": round(duration),
                "geometry": {"type": "LineString", "coordinates": geometry},
                "segments": segments,
                "checkpoints": checkpoints,
                "stats": segment_stats(checkpoints),
            }
        )

    ranked = policy.rank(routes)
    for index, route in enumerate(ranked, start=1):
        route["id"] = f"r{index}"
        route["recommended"] = index == 1

    heatmap = []
    for lat, lon in heat_cells:
        cell = {"lat": lat, "lon": lon, "risk": []}
        for hazard in policy.hazards:
            cell[hazard.name] = []
        for hour in range(HEATMAP_HOURS):
            when = departure + timedelta(hours=hour)
            readings = forecast.readings(lat, lon, when)
            level, _drivers = policy.classify(readings, load_lb)
            cell["risk"].append(SCORES[level] if level else 0)
            for hazard in policy.hazards:
                cell[hazard.name].append(_round(readings.get(hazard.name), hazard.digits))
        heatmap.append(cell)

    return {
        "origin": origin_hit,
        "destination": destination_hit,
        "departure": departure.isoformat(timespec="seconds"),
        "load_weight_lb": load_lb,
        "sample_miles": sample_miles,
        "routing_provider": provider,
        "warning": warning,
        "routes": ranked,
        "heatmap": {"points": heatmap, "spacing_miles": round(heat_spacing, 2)},
    }


def _resolve_places(origin, destination, origin_place, destination_place):
    if origin_place and destination_place:
        return origin_place, destination_place
    if origin_place:
        return origin_place, geocode(destination)
    if destination_place:
        return geocode(origin), destination_place
    return geocode_pair(origin, destination)


def _round(value, digits):
    if value is None:
        return None
    return round(value, digits)
