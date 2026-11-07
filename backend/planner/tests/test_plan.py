"""build_plan end to end with geocoding, routing, and weather stubbed out."""

from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase

from planner.services import plan as plan_module
from planner.services.plan import HEATMAP_HOURS, build_plan
from planner.services.risk import LEVEL_LOW, LEVEL_NO_TRAVEL, LEVEL_SEVERE

DEPARTURE = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
ORIGIN = {"label": "A", "lat": 40.0, "lon": -90.0}
DESTINATION = {"label": "B", "lat": 40.0, "lon": -88.0}


def _line(lat, steps=20):
    return [(lat, -90.0 + 2.0 * i / steps) for i in range(steps + 1)]


# Three east-west routes of ~105 miles. Route "north" has the shortest time.
ROUTES = [
    {"coordinates": _line(40.5), "duration_seconds": 6_000},  # north, fast
    {"coordinates": _line(40.0), "duration_seconds": 7_200},  # middle
    {"coordinates": _line(39.5), "duration_seconds": 9_000},  # south, slow
]


class FakeForecast:
    """Wind by latitude: north is stormy, middle is breezy, south is calm."""

    def __init__(self):
        self.calls = []

    def readings(self, lat, lon, when):
        self.calls.append((lat, lon, when))
        if lat > 40.25:
            wind = 50
        elif lat > 39.75:
            wind = 40
        else:
            wind = 5
        return {"wind": wind, "rain": 0.0, "snow": 0.0}


class BuildPlanTests(SimpleTestCase):
    def _build(self, load_lb=10_000, sample_miles=25):
        forecast = FakeForecast()
        with (
            mock.patch.object(plan_module, "geocode_pair", return_value=(ORIGIN, DESTINATION)),
            mock.patch.object(plan_module, "fetch_routes", return_value=("osrm", ROUTES, "car routes")),
            mock.patch.object(plan_module, "fetch_forecasts", return_value=forecast),
        ):
            result = build_plan("A", "B", DEPARTURE, load_lb, sample_miles, api_key=None)
        return result, forecast

    def test_returns_three_ranked_routes_with_one_recommendation(self):
        result, _ = self._build()
        self.assertEqual(len(result["routes"]), 3)
        self.assertEqual([r["id"] for r in result["routes"]], ["r1", "r2", "r3"])
        self.assertEqual([r["recommended"] for r in result["routes"]], [True, False, False])
        self.assertEqual(result["routing_provider"], "osrm")
        self.assertEqual(result["warning"], "car routes")

    def test_recommends_the_calm_route_over_the_fast_stormy_one(self):
        result, _ = self._build()
        best = result["routes"][0]
        self.assertEqual(best["duration_seconds"], 9_000)
        self.assertEqual(best["stats"]["severe_miles"], 0)
        self.assertEqual(result["routes"][-1]["duration_seconds"], 6_000)

    def test_heavy_load_turns_50_mph_into_no_travel(self):
        result, _ = self._build(load_lb=35_000)
        stormy = next(r for r in result["routes"] if r["duration_seconds"] == 6_000)
        self.assertGreater(stormy["stats"]["no_travel_miles"], 0)
        self.assertTrue(all(c["level"] == LEVEL_NO_TRAVEL for c in stormy["checkpoints"]))
        middle = next(r for r in result["routes"] if r["duration_seconds"] == 7_200)
        # 40 mph + >40,000 lb would be Severe; 35,000 lb stays High
        self.assertEqual(middle["stats"]["severe_miles"], 0)

    def test_checkpoints_follow_the_interval_and_eta_scales_with_miles(self):
        for interval in (10, 25, 50):
            with self.subTest(interval=interval):
                result, _ = self._build(sample_miles=interval)
                for route in result["routes"]:
                    checkpoints = route["checkpoints"]
                    self.assertEqual(checkpoints[0]["mile"], 0)
                    self.assertAlmostEqual(checkpoints[-1]["mile"], route["distance_miles"], delta=0.1)
                    for prev, nxt in zip(checkpoints, checkpoints[1:-1]):
                        self.assertAlmostEqual(nxt["mile"] - prev["mile"], interval, delta=0.01)
                    self.assertEqual(datetime.fromisoformat(checkpoints[0]["eta"]), DEPARTURE)
                    arrival = DEPARTURE + timedelta(seconds=route["duration_seconds"])
                    self.assertEqual(datetime.fromisoformat(checkpoints[-1]["eta"]), arrival)
                    middle = checkpoints[1]
                    fraction = middle["mile"] / checkpoints[-1]["mile"]
                    expected = DEPARTURE + timedelta(seconds=route["duration_seconds"] * fraction)
                    actual = datetime.fromisoformat(middle["eta"])
                    self.assertLessEqual(abs((actual - expected).total_seconds()), 1)

    def test_checkpoint_carries_readings_level_and_drivers(self):
        result, _ = self._build()
        calm = result["routes"][0]["checkpoints"][1]
        self.assertEqual(calm["wind_mph"], 5)
        self.assertEqual(calm["rain_in_hr"], 0)
        self.assertEqual(calm["snow_in_hr"], 0)
        self.assertEqual(calm["level"], LEVEL_LOW)
        self.assertEqual(calm["drivers"], ["wind", "rain", "snow"])
        stormy = result["routes"][-1]["checkpoints"][1]
        self.assertEqual((stormy["level"], stormy["drivers"]), (LEVEL_SEVERE, ["wind"]))

    def test_segments_connect_consecutive_checkpoints(self):
        result, _ = self._build()
        for route in result["routes"]:
            self.assertEqual(len(route["segments"]), len(route["checkpoints"]) - 1)
            for segment, end in zip(route["segments"], route["checkpoints"][1:]):
                self.assertEqual(segment["level"], end["level"])
                self.assertGreaterEqual(len(segment["coordinates"]), 2)

    def test_heatmap_has_49_hours_per_cell(self):
        result, forecast = self._build()
        points = result["heatmap"]["points"]
        self.assertGreater(len(points), 0)
        for point in points:
            for key in ("risk", "wind", "rain", "snow"):
                self.assertEqual(len(point[key]), HEATMAP_HOURS)
            self.assertTrue(all(1 <= score <= 5 for score in point["risk"]))
        cell_times = {when for lat, lon, when in forecast.calls if (lat, lon) == (points[0]["lat"], points[0]["lon"])}
        self.assertIn(DEPARTURE, cell_times)
        self.assertIn(DEPARTURE + timedelta(hours=48), cell_times)


class PickedPlaceTests(SimpleTestCase):
    def _run(self, **places):
        with (
            mock.patch.object(plan_module, "geocode_pair", return_value=(ORIGIN, DESTINATION)) as pair,
            mock.patch.object(plan_module, "geocode", return_value=DESTINATION) as single,
            mock.patch.object(plan_module, "fetch_routes", return_value=("osrm", ROUTES, None)) as routes,
            mock.patch.object(plan_module, "fetch_forecasts", return_value=FakeForecast()),
        ):
            result = build_plan("A", "B", DEPARTURE, 10_000, 50, api_key=None, **places)
        return result, pair, single, routes

    def test_both_places_skip_geocoding(self):
        picked = {"label": "Picked A", "lat": 40.1, "lon": -90.1}
        result, pair, single, routes = self._run(origin_place=picked, destination_place=DESTINATION)
        pair.assert_not_called()
        single.assert_not_called()
        self.assertEqual(result["origin"], picked)
        self.assertEqual(routes.call_args.args[0], picked)

    def test_one_place_geocodes_only_the_other(self):
        picked = {"label": "Picked A", "lat": 40.1, "lon": -90.1}
        _result, pair, single, _routes = self._run(origin_place=picked)
        pair.assert_not_called()
        single.assert_called_once_with("B")


class HeatmapBudgetTests(SimpleTestCase):
    def test_heatmap_stays_within_the_budget(self):
        forecast = FakeForecast()
        for budget in (40, 120):
            with self.subTest(budget=budget), self.settings(HEATMAP_CELL_BUDGET=budget):
                with (
                    mock.patch.object(plan_module, "geocode_pair", return_value=(ORIGIN, DESTINATION)),
                    mock.patch.object(plan_module, "fetch_routes", return_value=("osrm", ROUTES, None)),
                    mock.patch.object(plan_module, "fetch_forecasts", return_value=forecast),
                ):
                    result = build_plan("A", "B", DEPARTURE, 10_000, 25, api_key=None)
                points = result["heatmap"]["points"]
                self.assertLessEqual(len(points), budget)
                self.assertGreater(len(points), budget * 0.5)
                self.assertGreater(result["heatmap"]["spacing_miles"], 0)
