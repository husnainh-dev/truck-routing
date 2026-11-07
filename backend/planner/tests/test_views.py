from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from planner.services.errors import GeocodeError, RoutingError, WeatherError

URL = "/api/plan/"


def _body(**overrides):
    body = {
        "origin": "Chicago, IL",
        "destination": "St. Louis, MO",
        "departure": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "load_weight_lb": 35_000,
        "sample_miles": 25,
    }
    body.update(overrides)
    return body


@mock.patch("planner.views.build_plan", return_value={"routes": []})
class PlanViewTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_valid_request_is_passed_to_the_planner(self, build_plan):
        response = self.client.post(URL, _body(sample_miles=10), format="json")
        self.assertEqual(response.status_code, 200)
        args = build_plan.call_args.args
        self.assertEqual(args[0:2], ("Chicago, IL", "St. Louis, MO"))
        self.assertEqual(args[2].tzinfo, timezone.utc)
        self.assertEqual(args[3], 35_000)
        self.assertEqual(args[4], 10)

    def test_sample_interval_defaults_to_25(self, build_plan):
        body = _body()
        del body["sample_miles"]
        self.client.post(URL, body, format="json")
        self.assertEqual(build_plan.call_args.args[4], 25)

    def test_invalid_input_is_a_400(self, build_plan):
        cases = {
            "missing origin": _body(origin=""),
            "missing destination": _body(destination="  "),
            "bad departure": _body(departure="tomorrow"),
            "past departure": _body(departure=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
            "too far ahead": _body(departure=(datetime.now(timezone.utc) + timedelta(days=8)).isoformat()),
            "missing load": _body(load_weight_lb=None),
            "text load": _body(load_weight_lb="heavy"),
            "negative load": _body(load_weight_lb=-1),
            "bad interval": _body(sample_miles=15),
        }
        for name, body in cases.items():
            with self.subTest(name):
                response = self.client.post(URL, body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        build_plan.assert_not_called()

    def test_service_errors_map_to_status_codes(self, build_plan):
        for error, status in ((GeocodeError("x"), 400), (RoutingError("x"), 502), (WeatherError("x"), 502)):
            with self.subTest(error=type(error).__name__):
                build_plan.side_effect = error
                response = self.client.post(URL, _body(), format="json")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"error": "x"})


@mock.patch("planner.views.build_plan", return_value={"routes": []})
class PickedPlaceTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_picked_places_are_forwarded(self, build_plan):
        body = _body(
            origin_place={"label": "Chicago, IL, USA", "lat": 41.88, "lon": -87.63},
            destination_place={"lat": "38.63", "lon": "-90.2"},
        )
        response = self.client.post(URL, body, format="json")
        self.assertEqual(response.status_code, 200)
        kwargs = build_plan.call_args.kwargs
        self.assertEqual(kwargs["origin_place"], {"label": "Chicago, IL, USA", "lat": 41.88, "lon": -87.63})
        # label falls back to the typed text
        self.assertEqual(kwargs["destination_place"], {"label": "St. Louis, MO", "lat": 38.63, "lon": -90.2})

    def test_places_are_optional(self, build_plan):
        self.client.post(URL, _body(origin_place=None), format="json")
        self.assertIsNone(build_plan.call_args.kwargs["origin_place"])
        self.assertIsNone(build_plan.call_args.kwargs["destination_place"])

    def test_bad_places_are_a_400(self, build_plan):
        for place in ("Chicago", {"lat": 41}, {"lat": "x", "lon": 1}, {"lat": 95, "lon": 0}):
            with self.subTest(place=place):
                response = self.client.post(URL, _body(origin_place=place), format="json")
                self.assertEqual(response.status_code, 400)
        build_plan.assert_not_called()


class HealthTests(SimpleTestCase):
    def test_health_check_is_ok(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
