"""Polyline distances, checkpoint spacing, and corridor offsets."""

import math

EARTH_RADIUS_MILES = 3958.7613
CORRIDOR_SPACING_MILES = 15
CORRIDOR_OFFSETS_MILES = (5, 10, 15)


def haversine_miles(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    arc = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
    return EARTH_RADIUS_MILES * arc


def cumulative_miles(coords):
    totals = [0.0]
    for previous, current in zip(coords, coords[1:]):
        totals.append(totals[-1] + haversine_miles(previous[0], previous[1], current[0], current[1]))
    return totals


def _segment_index(cumulative, target):
    """Smallest index >= 1 whose cumulative mile is at or past target."""
    if len(cumulative) < 2:
        return 0
    if target <= 0:
        return 1
    if target >= cumulative[-1]:
        return len(cumulative) - 1
    low = 1
    high = len(cumulative) - 1
    while low < high:
        mid = (low + high) // 2
        if cumulative[mid] < target:
            low = mid + 1
        else:
            high = mid
    return low


def point_at_mile(coords, cumulative, target):
    if target <= 0:
        return coords[0]
    if target >= cumulative[-1]:
        return coords[-1]
    index = _segment_index(cumulative, target)
    start = cumulative[index - 1]
    span = cumulative[index] - start
    if span <= 0:
        return coords[index]
    fraction = (target - start) / span
    lat1, lon1 = coords[index - 1]
    lat2, lon2 = coords[index]
    return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)


def bearing_degrees(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def bearing_at_mile(coords, cumulative, target):
    if len(coords) < 2:
        return 0.0
    index = _segment_index(cumulative, target)
    if index == 0:
        return 0.0
    return bearing_degrees(
        coords[index - 1][0],
        coords[index - 1][1],
        coords[index][0],
        coords[index][1],
    )


def offset_point(lat, lon, bearing_deg, distance_miles):
    angular = distance_miles / EARTH_RADIUS_MILES
    bearing = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    phi2 = math.asin(
        math.sin(phi1) * math.cos(angular)
        + math.cos(phi1) * math.sin(angular) * math.cos(bearing)
    )
    lambda2 = lambda1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(phi1),
        math.cos(angular) - math.sin(phi1) * math.sin(phi2),
    )
    lon2 = (math.degrees(lambda2) + 540) % 360 - 180
    return (math.degrees(phi2), lon2)


def sample_checkpoints(coords, interval_miles):
    """Points along the road, including the start and the end."""
    if not coords:
        return [], [0.0]
    cumulative = cumulative_miles(coords)
    total = cumulative[-1]
    if total == 0 or interval_miles <= 0:
        lat, lon = coords[0]
        return [{"lat": lat, "lon": lon, "mile": 0.0}], cumulative
    targets = []
    mile = 0.0
    while mile < total - 1e-6:
        targets.append(mile)
        mile += interval_miles
    targets.append(total)
    points = []
    for target in targets:
        lat, lon = point_at_mile(coords, cumulative, target)
        points.append({"lat": lat, "lon": lon, "mile": target})
    return points, cumulative


def subline_coordinates(coords, cumulative, start_mile, end_mile):
    """GeoJSON [lon, lat] positions covering start_mile to end_mile."""
    if end_mile < start_mile:
        start_mile, end_mile = end_mile, start_mile
    total = cumulative[-1] if cumulative else 0.0
    start_mile = min(max(0.0, start_mile), total)
    end_mile = min(max(0.0, end_mile), total)
    lat, lon = point_at_mile(coords, cumulative, start_mile)
    points = [[round(lon, 5), round(lat, 5)]]
    for index, mile in enumerate(cumulative):
        if start_mile + 1e-6 < mile < end_mile - 1e-6:
            vertex_lat, vertex_lon = coords[index]
            points.append([round(vertex_lon, 5), round(vertex_lat, 5)])
    lat, lon = point_at_mile(coords, cumulative, end_mile)
    end = [round(lon, 5), round(lat, 5)]
    if points[-1] != end:
        points.append(end)
    if len(points) == 1:
        points.append(points[0])
    return points


def corridor_samples(coords, spacing=CORRIDOR_SPACING_MILES, offsets=CORRIDOR_OFFSETS_MILES):
    """Centerline samples plus points to either side of the road."""
    checkpoints, cumulative = sample_checkpoints(coords, spacing)
    samples = []
    for checkpoint in checkpoints:
        lat = checkpoint["lat"]
        lon = checkpoint["lon"]
        samples.append((lat, lon))
        if len(coords) < 2:
            continue
        bearing = bearing_at_mile(coords, cumulative, checkpoint["mile"])
        for offset in offsets:
            samples.append(offset_point(lat, lon, bearing + 90, offset))
            samples.append(offset_point(lat, lon, bearing - 90, offset))
    return samples


def nearest_vertex_miles(lat, lon, coords):
    if not coords:
        return float("inf")
    step = max(1, len(coords) // 400)
    best = float("inf")
    for index in range(0, len(coords), step):
        other_lat, other_lon = coords[index]
        distance = haversine_miles(lat, lon, other_lat, other_lon)
        if distance < best:
            best = distance
    return best


CORRIDOR_HALF_WIDTH_MILES = 15
CORRIDOR_ROUTE_STEP_MILES = 2


def corridor_grid(routes, budget, half_width=CORRIDOR_HALF_WIDTH_MILES):
    """An even lat/lon grid covering every point within ``half_width`` miles of any route.

    The spacing is chosen so the grid has at most ``budget`` points. Returns
    (points, spacing_miles). An even grid covers the corridor far more
    smoothly than rows of samples across the road for the same number of
    weather locations.
    """
    route_points = []
    for coords in routes:
        if len(coords) < 2:
            continue
        samples, _ = sample_checkpoints(coords, CORRIDOR_ROUTE_STEP_MILES)
        route_points.extend((point["lat"], point["lon"]) for point in samples)
    if not route_points or budget <= 0:
        return [], 0.0

    lats = [lat for lat, _ in route_points]
    lons = [lon for _, lon in route_points]
    mid_lat = (min(lats) + max(lats)) / 2
    miles_per_lon = 69.172 * max(0.1, math.cos(math.radians(mid_lat)))
    miles_per_lat = 69.0
    pad_lat = half_width / miles_per_lat
    pad_lon = half_width / miles_per_lon
    south, north = min(lats) - pad_lat, max(lats) + pad_lat
    west, east = min(lons) - pad_lon, max(lons) + pad_lon

    # Bucket route points by a coarse cell so each grid point checks only its neighbours.
    bucket_lat = half_width / miles_per_lat
    bucket_lon = half_width / miles_per_lon
    buckets = {}
    for lat, lon in route_points:
        buckets.setdefault((int((lat - south) // bucket_lat), int((lon - west) // bucket_lon)), []).append((lat, lon))

    def near_route(lat, lon):
        row = int((lat - south) // bucket_lat)
        col = int((lon - west) // bucket_lon)
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                for other_lat, other_lon in buckets.get((row + d_row, col + d_col), ()):
                    if haversine_miles(lat, lon, other_lat, other_lon) <= half_width:
                        return True
        return False

    def grid(spacing):
        step_lat = spacing / miles_per_lat
        step_lon = spacing / miles_per_lon
        points = []
        rows = int((north - south) / step_lat) + 1
        cols = int((east - west) / step_lon) + 1
        for row in range(rows):
            lat = south + row * step_lat
            # Offset every other row for a hexagonal-ish pattern.
            shift = step_lon / 2 if row % 2 else 0.0
            for col in range(cols):
                lon = west + shift + col * step_lon
                if near_route(lat, lon):
                    points.append((lat, lon))
        return points

    # First guess from the corridor area, then widen until within budget.
    total_miles = sum(cumulative_miles(coords)[-1] for coords in routes if len(coords) >= 2)
    area = max(1.0, total_miles * 2 * half_width)
    spacing = max(2.0, math.sqrt(area / budget) * 0.6)
    points = grid(spacing)
    while len(points) > budget:
        spacing *= 1.12
        points = grid(spacing)
    return points, spacing
