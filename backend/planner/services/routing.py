import requests

from .errors import RoutingError
from .sample import (
    bearing_at_mile,
    cumulative_miles,
    haversine_miles,
    nearest_vertex_miles,
    offset_point,
    point_at_mile,
)

ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
VIA_OFFSETS_MILES = (25, 45)
# ORS's public API only computes alternative routes up to 100 km.
ORS_ALTERNATIVES_MAX_MILES = 100 / 1.609344


def fetch_routes(origin, destination, api_key):
    """Return (provider, routes, warning).

    With an OpenRouteService key, three ``driving-hgv`` truck routes are tried
    first. ORS only computes alternatives for routes under 100 km, so longer
    trips take the main truck route and add detours through points offset to
    either side of it. If that still gives fewer than three truck routes, the
    public OSRM car service is used, filled the same way.
    """
    warning = None
    if api_key:
        try:
            truck_routes = _ors_truck_routes(origin, destination, api_key)
        except RoutingError as exc:
            truck_routes = []
            warning = str(exc)
        if len(truck_routes) >= 3:
            return "openrouteservice", truck_routes[:3], None
        if truck_routes:
            warning = (
                f"Truck routing found {len(truck_routes)} "
                f"{'route' if len(truck_routes) == 1 else 'routes'} instead of 3, "
                "so car alternatives are shown instead."
            )
        elif warning is None:
            warning = "Truck routing returned no routes, so car alternatives are shown instead."
        else:
            warning = f"{warning} Car alternatives are shown instead."
    else:
        warning = "No OpenRouteService key is set, so these are car routes rather than truck routes."

    try:
        routes = _osrm_pair(origin, destination)
    except RoutingError as exc:
        raise RoutingError("Could not find a driving route between those places.") from exc

    routes = _fill_with_via_routes(origin, destination, routes, _osrm_via)
    if not routes:
        raise RoutingError("Could not find a driving route between those places.")
    if len(routes) < 3:
        warning = f"{warning} Only {len(routes)} {'route' if len(routes) == 1 else 'routes'} available."
    return "osrm", routes[:3], warning.strip()


def _ors_truck_routes(origin, destination, api_key):
    ends = [(origin["lon"], origin["lat"]), (destination["lon"], destination["lat"])]
    routes = []
    # Straight-line distance is a lower bound on road distance, so a long
    # trip is known to exceed the alternatives limit without asking.
    if haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"]) < ORS_ALTERNATIVES_MAX_MILES:
        try:
            routes = _ors_points(ends, api_key, alternatives=True)
        except RoutingError:
            routes = []
    if not routes:
        routes = _ors_points(ends, api_key, alternatives=False)
    return _fill_with_via_routes(
        origin,
        destination,
        routes,
        lambda points: _ors_points(points, api_key, alternatives=False),
    )


def _ors_points(points, api_key, alternatives):
    body = {"coordinates": [[lon, lat] for lon, lat in points]}
    if len(points) > 2:
        # ORS only snaps a point to a road within 350 m by default. Detour
        # points can land in fields or lakes, so let the via points snap to the
        # nearest road at any distance; the two ends keep the default.
        body["radiuses"] = [350] + [-1] * (len(points) - 2) + [350]
    if alternatives:
        body["alternative_routes"] = {
            "target_count": 3,
            "share_factor": 0.6,
            "weight_factor": 1.4,
        }
    try:
        response = requests.post(
            ORS_URL,
            json=body,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RoutingError("Truck routing could not be reached.") from exc
    if response.status_code in (401, 403):
        raise RoutingError("The OpenRouteService key was rejected.")
    if response.status_code == 429:
        raise RoutingError("The OpenRouteService quota is used up for now.")
    if response.status_code >= 400:
        raise RoutingError("Truck routing rejected the request.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RoutingError("Truck routing returned an unreadable response.") from exc
    routes = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        coordinates = _latlon_pairs(geometry.get("coordinates") or [])
        if len(coordinates) < 2:
            continue
        summary = (feature.get("properties") or {}).get("summary") or {}
        routes.append(
            {
                "coordinates": coordinates,
                "duration_seconds": float(summary.get("duration") or 0),
            }
        )
    return routes


def _osrm_pair(origin, destination):
    return _osrm_points(
        [(origin["lon"], origin["lat"]), (destination["lon"], destination["lat"])],
        alternatives=True,
    )


def _osrm_points(points, alternatives):
    path = ";".join(f"{lon},{lat}" for lon, lat in points)
    try:
        response = requests.get(
            f"{OSRM_URL}/{path}",
            params={
                "alternatives": "true" if alternatives else "false",
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RoutingError("Driving directions could not be reached.") from exc
    except ValueError as exc:
        raise RoutingError("Driving directions returned an unreadable response.") from exc
    if payload.get("code") != "Ok":
        raise RoutingError(payload.get("message") or "Driving directions failed.")
    routes = []
    for route in payload.get("routes") or []:
        geometry = route.get("geometry") or {}
        coordinates = _latlon_pairs(geometry.get("coordinates") or [])
        if len(coordinates) < 2:
            continue
        routes.append(
            {
                "coordinates": coordinates,
                "duration_seconds": float(route.get("duration") or 0),
            }
        )
    return routes


def _osrm_via(points):
    return _osrm_points(points, alternatives=False)


def _fill_with_via_routes(origin, destination, routes, fetch):
    """Add routes through points offset from the first route's midpoint until there are three.

    ``fetch`` takes [(lon, lat), ...] stops and returns routes. Detours that
    are much slower than the main route or that overlap an existing route are
    skipped.
    """
    routes = list(routes)
    if len(routes) >= 3 or not routes:
        return routes
    primary = routes[0]
    cumulative = cumulative_miles(primary["coordinates"])
    total = cumulative[-1]
    if total <= 0:
        return routes
    midpoint = point_at_mile(primary["coordinates"], cumulative, total / 2)
    bearing = bearing_at_mile(primary["coordinates"], cumulative, total / 2)
    limit = primary["duration_seconds"] * 1.85 or 1
    for distance in VIA_OFFSETS_MILES:
        for turn in (90, -90):
            if len(routes) >= 3:
                return routes
            via_lat, via_lon = offset_point(midpoint[0], midpoint[1], bearing + turn, distance)
            try:
                candidates = fetch(
                    [
                        (origin["lon"], origin["lat"]),
                        (via_lon, via_lat),
                        (destination["lon"], destination["lat"]),
                    ]
                )
            except RoutingError:
                continue
            for candidate in candidates:
                if candidate["duration_seconds"] > limit:
                    continue
                if _is_distinct(candidate, routes):
                    routes.append(candidate)
                    break
    return routes


def _is_distinct(candidate, existing):
    coords = candidate["coordinates"]
    step = max(1, len(coords) // 16)
    far_samples = 0
    for lat, lon in coords[::step]:
        nearest = min(nearest_vertex_miles(lat, lon, route["coordinates"]) for route in existing)
        if nearest >= 6:
            far_samples += 1
        if far_samples >= 2:
            return True
    return False


def _latlon_pairs(coordinates):
    pairs = []
    for pair in coordinates:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        lon, lat = pair[0], pair[1]
        pairs.append((float(lat), float(lon)))
    return pairs
