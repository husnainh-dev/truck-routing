from unittest import mock

from django.test import SimpleTestCase

from planner.services import routing
from planner.services.errors import RoutingError
from planner.services.routing import fetch_routes

# ~105 miles apart: over the 100 km ORS alternatives limit.
A = {"lat": 40.0, "lon": -90.0}
B = {"lat": 40.0, "lon": -88.0}
# ~25 miles apart: under the limit.
NEAR = {"lat": 40.0, "lon": -89.55}


def _route(lat, duration=3_600):
    return {"coordinates": [(lat, -90.0), (lat, -88.0)], "duration_seconds": duration}


def _response(status, payload=None):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload or {}
    return response


class TruckRoutingTests(SimpleTestCase):
    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points")
    def test_short_trip_uses_ors_alternatives(self, ors, osrm):
        ors.return_value = [_route(40), _route(41), _route(39), _route(42)]
        provider, routes, warning = fetch_routes(A, NEAR, "key")
        self.assertEqual((provider, len(routes), warning), ("openrouteservice", 3, None))
        self.assertTrue(ors.call_args.kwargs["alternatives"])
        osrm.assert_not_called()

    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points")
    def test_long_trip_skips_alternatives_and_adds_truck_detours(self, ors, osrm):
        ors.side_effect = [[_route(40.0)], [_route(40.6, 4_000)], [_route(39.4, 4_200)]]
        provider, routes, warning = fetch_routes(A, B, "key")
        self.assertEqual((provider, len(routes), warning), ("openrouteservice", 3, None))
        first, *detours = ors.call_args_list
        self.assertFalse(first.kwargs["alternatives"])
        self.assertEqual(len(first.args[0]), 2)
        for call in detours:
            self.assertEqual(len(call.args[0]), 3)  # origin, via, destination
            self.assertFalse(call.kwargs["alternatives"])
        osrm.assert_not_called()

    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points")
    def test_short_trip_whose_road_is_too_long_retries_without_alternatives(self, ors, osrm):
        ors.side_effect = [RoutingError("too long"), [_route(40.0)], [_route(40.6, 4_000)], [_route(39.4, 4_200)]]
        provider, routes, _warning = fetch_routes(A, NEAR, "key")
        self.assertEqual((provider, len(routes)), ("openrouteservice", 3))
        self.assertTrue(ors.call_args_list[0].kwargs["alternatives"])
        self.assertFalse(ors.call_args_list[1].kwargs["alternatives"])

    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points")
    def test_too_few_truck_routes_fall_back_to_car(self, ors, osrm):
        ors.side_effect = [[_route(40.0)]] + [RoutingError("no")] * 4
        osrm.return_value = [_route(40), _route(41), _route(39)]
        provider, routes, warning = fetch_routes(A, B, "key")
        self.assertEqual((provider, len(routes)), ("osrm", 3))
        self.assertIn("found 1 route instead of 3", warning)

    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points", side_effect=RoutingError("The OpenRouteService key was rejected."))
    def test_ors_failure_falls_back_to_osrm(self, _ors, osrm):
        osrm.return_value = [_route(40), _route(41), _route(39)]
        provider, _routes, warning = fetch_routes(A, B, "key")
        self.assertEqual(provider, "osrm")
        self.assertIn("key was rejected", warning)
        self.assertIn("Car alternatives", warning)

    @mock.patch.object(routing.requests, "post")
    def test_ors_errors_are_explained(self, post):
        for status, text in ((401, "key was rejected"), (403, "key was rejected"), (429, "quota"), (400, "rejected")):
            with self.subTest(status=status):
                post.return_value = _response(status)
                with self.assertRaisesRegex(RoutingError, text):
                    routing._ors_points([(0, 0), (1, 1)], "key", alternatives=False)

    @mock.patch.object(routing.requests, "post")
    def test_ors_request_shape(self, post):
        post.return_value = _response(200, {"features": [{
            "geometry": {"type": "LineString", "coordinates": [[-90, 40], [-88, 40]]},
            "properties": {"summary": {"duration": 3600}},
        }]})
        routes = routing._ors_points([(-90, 40), (-89, 40.5), (-88, 40)], "secret", alternatives=False)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["coordinates"], [[-90, 40], [-89, 40.5], [-88, 40]])
        self.assertNotIn("alternative_routes", body)
        self.assertEqual(body["radiuses"], [350, -1, 350])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "secret")
        self.assertEqual(routes, [{"coordinates": [(40.0, -90.0), (40.0, -88.0)], "duration_seconds": 3600.0}])


class CarRoutingTests(SimpleTestCase):
    @mock.patch.object(routing, "_osrm_pair")
    @mock.patch.object(routing, "_ors_points")
    def test_without_a_key_car_routes_are_used_with_a_warning(self, ors, osrm):
        osrm.return_value = [_route(40), _route(41), _route(39)]
        provider, routes, warning = fetch_routes(A, B, "")
        self.assertEqual((provider, len(routes)), ("osrm", 3))
        self.assertIn("car routes", warning)
        ors.assert_not_called()

    @mock.patch.object(routing, "_osrm_points")
    @mock.patch.object(routing, "_osrm_pair")
    def test_missing_alternatives_are_filled_with_via_routes(self, osrm_pair, osrm_points):
        osrm_pair.return_value = [_route(40.0)]
        osrm_points.side_effect = [[_route(40.6, 4_000)], [_route(39.4, 4_200)]]
        _provider, routes, _warning = fetch_routes(A, B, "")
        self.assertEqual(len(routes), 3)
        for call in osrm_points.call_args_list:
            self.assertEqual(len(call.args[0]), 3)

    @mock.patch.object(routing, "_osrm_points", side_effect=RoutingError("no"))
    @mock.patch.object(routing, "_osrm_pair", return_value=[_route(40.0)])
    def test_fewer_than_three_routes_is_reported(self, _pair, _points):
        _provider, routes, warning = fetch_routes(A, B, "")
        self.assertEqual(len(routes), 1)
        self.assertIn("Only 1 route available", warning)

    @mock.patch.object(routing, "_osrm_pair", side_effect=RoutingError("down"))
    def test_no_route_at_all_raises(self, _pair):
        with self.assertRaises(RoutingError):
            fetch_routes(A, B, "")

    def test_slow_or_overlapping_detours_are_skipped(self):
        primary = _route(40.0, 3_600)
        fetch = mock.Mock(side_effect=[[_route(40.6, 3_600 * 3)], [_route(40.0, 3_700)], [_route(39.4, 4_000)], []])
        routes = routing._fill_with_via_routes(A, B, [primary], fetch)
        self.assertEqual(len(routes), 2)  # too slow and duplicate were dropped
        self.assertEqual(routes[1]["coordinates"][0][0], 39.4)

    def test_far_away_candidate_is_distinct_and_overlap_is_not(self):
        existing = [_route(40.0)]
        self.assertTrue(routing._is_distinct(_route(41.0), existing))
        self.assertFalse(routing._is_distinct(_route(40.0), existing))
