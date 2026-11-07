from django.test import SimpleTestCase

from planner.services.risk import (
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MODERATE,
    LEVEL_NO_TRAVEL,
    LEVEL_SEVERE,
    classify_checkpoint,
    classify_rain,
    classify_snow,
    classify_wind,
    rank_routes,
    segment_stats,
)


class WindBandTests(SimpleTestCase):
    def test_edges_for_a_light_load(self):
        load = 10_000
        self.assertEqual(classify_wind(24.9, load), LEVEL_LOW)
        self.assertEqual(classify_wind(25, load), LEVEL_MODERATE)
        self.assertEqual(classify_wind(34.9, load), LEVEL_MODERATE)
        self.assertEqual(classify_wind(35, load), LEVEL_HIGH)
        self.assertEqual(classify_wind(44.9, load), LEVEL_HIGH)
        self.assertEqual(classify_wind(45, load), LEVEL_SEVERE)
        self.assertEqual(classify_wind(54.9, load), LEVEL_SEVERE)
        self.assertEqual(classify_wind(55, load), LEVEL_NO_TRAVEL)

    def test_load_overrides_use_strictly_greater_than(self):
        self.assertEqual(classify_wind(40, 40_000), LEVEL_HIGH)
        self.assertEqual(classify_wind(40, 40_001), LEVEL_SEVERE)
        self.assertEqual(classify_wind(50, 30_000), LEVEL_SEVERE)
        self.assertEqual(classify_wind(50, 30_001), LEVEL_NO_TRAVEL)
        self.assertEqual(classify_wind(55, 1), LEVEL_NO_TRAVEL)
        self.assertEqual(classify_wind(60, 80_000), LEVEL_NO_TRAVEL)


class PrecipitationBandTests(SimpleTestCase):
    def test_rain_boundaries_fall_in_the_more_severe_band(self):
        self.assertEqual(classify_rain(0.09), LEVEL_LOW)
        self.assertEqual(classify_rain(0.10), LEVEL_MODERATE)
        self.assertEqual(classify_rain(0.25), LEVEL_HIGH)
        self.assertEqual(classify_rain(0.50), LEVEL_SEVERE)
        self.assertEqual(classify_rain(1.00), LEVEL_SEVERE)
        self.assertEqual(classify_rain(1.01), LEVEL_NO_TRAVEL)

    def test_snow_boundaries_fall_in_the_more_severe_band(self):
        self.assertEqual(classify_snow(0.49), LEVEL_LOW)
        self.assertEqual(classify_snow(0.5), LEVEL_MODERATE)
        self.assertEqual(classify_snow(1.0), LEVEL_HIGH)
        self.assertEqual(classify_snow(2.0), LEVEL_SEVERE)
        self.assertEqual(classify_snow(3.0), LEVEL_SEVERE)
        self.assertEqual(classify_snow(3.01), LEVEL_NO_TRAVEL)


class CheckpointTests(SimpleTestCase):
    def test_worst_hazard_wins_and_ties_list_every_driver(self):
        level, drivers = classify_checkpoint(10, 0.6, 0.1, 10_000)
        self.assertEqual(level, LEVEL_SEVERE)
        self.assertEqual(drivers, ["rain"])

        level, drivers = classify_checkpoint(50, 0.8, None, 10_000)
        self.assertEqual(level, LEVEL_SEVERE)
        self.assertEqual(drivers, ["wind", "rain"])

    def test_missing_readings_are_ignored(self):
        level, drivers = classify_checkpoint(None, 0, None, 10_000)
        self.assertEqual(level, LEVEL_LOW)
        self.assertEqual(drivers, ["rain"])
        self.assertEqual(classify_checkpoint(None, None, None, 10_000), (None, []))


class SegmentStatTests(SimpleTestCase):
    def test_end_checkpoint_owns_the_segment_miles(self):
        stats = segment_stats(
            [
                {"mile": 0, "level": LEVEL_LOW},
                {"mile": 10, "level": LEVEL_HIGH},
                {"mile": 30, "level": LEVEL_SEVERE},
            ]
        )
        self.assertEqual(stats["high_miles"], 10)
        self.assertEqual(stats["severe_miles"], 20)
        self.assertEqual(stats["low_miles"], 0)
        self.assertEqual(stats["avg_risk"], round((3 * 10 + 4 * 20) / 30, 3))

    def test_unknown_segment_is_left_out_of_the_average(self):
        stats = segment_stats(
            [
                {"mile": 0, "level": LEVEL_LOW},
                {"mile": 10, "level": None},
                {"mile": 20, "level": LEVEL_LOW},
            ]
        )
        self.assertEqual(stats["low_miles"], 10)
        self.assertEqual(stats["avg_risk"], 1)


class RankTests(SimpleTestCase):
    def _route(self, name, no_travel, severe, high, average, duration):
        return {
            "name": name,
            "duration_seconds": duration,
            "stats": {
                "no_travel_miles": no_travel,
                "severe_miles": severe,
                "high_miles": high,
                "avg_risk": average,
            },
        }

    def test_no_travel_miles_outrank_a_shorter_cleaner_trip(self):
        safer = self._route("safer", 0, 10, 5, 2, 100)
        blocked = self._route("blocked", 5, 0, 0, 1, 50)
        ranked = rank_routes([blocked, safer])
        self.assertEqual([route["name"] for route in ranked], ["safer", "blocked"])

    def test_severe_then_high_then_average_then_time(self):
        more_severe = self._route("more-severe", 0, 8, 0, 1, 10)
        fewer_severe = self._route("fewer-severe", 0, 2, 40, 3, 900)
        self.assertEqual(rank_routes([more_severe, fewer_severe])[0]["name"], "fewer-severe")

        more_high = self._route("more-high", 0, 2, 20, 1, 10)
        fewer_high = self._route("fewer-high", 0, 2, 5, 3, 900)
        self.assertEqual(rank_routes([more_high, fewer_high])[0]["name"], "fewer-high")

        higher_avg = self._route("higher-avg", 0, 2, 5, 2.4, 10)
        lower_avg = self._route("lower-avg", 0, 2, 5, 1.6, 900)
        self.assertEqual(rank_routes([higher_avg, lower_avg])[0]["name"], "lower-avg")

        slower = self._route("slower", 0, 2, 5, 1.6, 400)
        faster = self._route("faster", 0, 2, 5, 1.6, 250)
        self.assertEqual(rank_routes([slower, faster])[0]["name"], "faster")
