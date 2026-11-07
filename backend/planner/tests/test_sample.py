from django.test import SimpleTestCase

from planner.services.sample import (
    cumulative_miles,
    offset_point,
    sample_checkpoints,
    subline_coordinates,
)


class SampleTests(SimpleTestCase):
    def test_checkpoints_include_both_ends_and_keep_the_interval(self):
        coords = [(0.0, lon / 10) for lon in range(0, 11)]
        points, cumulative = sample_checkpoints(coords, 25)
        self.assertEqual(points[0]["mile"], 0)
        self.assertAlmostEqual(points[-1]["mile"], cumulative[-1], places=3)
        self.assertGreater(cumulative[-1], 60)
        gaps = [nxt["mile"] - prev["mile"] for prev, nxt in zip(points, points[1:])]
        for gap in gaps[:-1]:
            self.assertAlmostEqual(gap, 25, delta=0.05)
        self.assertLessEqual(gaps[-1], 25)
        self.assertGreater(gaps[-1], 0)

    def test_short_route_still_has_start_and_end(self):
        coords = [(41.8, -87.6), (41.9, -87.6)]
        points, _cumulative = sample_checkpoints(coords, 50)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["mile"], 0)
        self.assertGreater(points[-1]["mile"], 0)

    def test_subline_covers_the_requested_span(self):
        coords = [(0.0, 0.0), (0.0, 1.0)]
        cumulative = cumulative_miles(coords)
        line = subline_coordinates(coords, cumulative, 0, cumulative[-1])
        self.assertEqual(line[0], [0.0, 0.0])
        self.assertEqual(line[-1][0], 1.0)
        self.assertEqual(line[-1][1], 0.0)

    def test_north_offset_increases_latitude(self):
        lat, lon = offset_point(0, 0, 0, 69)
        self.assertAlmostEqual(lat, 1, delta=0.02)
        self.assertAlmostEqual(lon, 0, delta=0.02)


class CorridorGridTests(SimpleTestCase):
    # ~105 mile east-west road and a parallel one ~35 miles north.
    ROAD = [(40.0, -90.0 + i * 0.1) for i in range(21)]
    NORTH = [(40.5, -90.0 + i * 0.1) for i in range(21)]

    def test_points_stay_inside_the_corridor_and_budget(self):
        from planner.services.sample import corridor_grid, haversine_miles

        points, spacing = corridor_grid([self.ROAD], budget=100, half_width=15)
        self.assertLessEqual(len(points), 100)
        self.assertGreater(len(points), 50)
        self.assertGreater(spacing, 0)
        for lat, lon in points:
            nearest = min(haversine_miles(lat, lon, r_lat, r_lon) for r_lat, r_lon in self.ROAD)
            self.assertLessEqual(nearest, 15 + 2)  # route sampled every 2 mi

    def test_grid_is_even_along_the_road(self):
        from planner.services.sample import corridor_grid

        points, spacing = corridor_grid([self.ROAD], budget=100, half_width=15)
        on_road_row = sorted(lon for lat, lon in points if abs(lat - 40.0) < spacing / 69 / 2)
        gaps = [(b - a) * 69.172 * 0.766 for a, b in zip(on_road_row, on_road_row[1:])]
        self.assertTrue(gaps)
        self.assertLess(max(gaps) - min(gaps), 0.5)

    def test_more_routes_share_one_budget(self):
        from planner.services.sample import corridor_grid

        single, _ = corridor_grid([self.ROAD], budget=100)
        both, _ = corridor_grid([self.ROAD, self.NORTH], budget=100)
        self.assertLessEqual(len(both), 100)
        self.assertGreater(len(single), 0)

    def test_empty_input(self):
        from planner.services.sample import corridor_grid

        self.assertEqual(corridor_grid([], budget=100), ([], 0.0))
        self.assertEqual(corridor_grid([self.ROAD], budget=0), ([], 0.0))
