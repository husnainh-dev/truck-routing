"""Spec table coverage and extension points of RiskPolicy."""

from django.test import SimpleTestCase

from planner.services.risk import (
    DEFAULT_POLICY,
    HAZARDS,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MODERATE,
    LEVEL_NO_TRAVEL,
    LEVEL_SEVERE,
    LOAD_RULES,
    RANKING,
    Hazard,
    LoadRule,
    RankCriterion,
    RiskPolicy,
    Threshold,
    classify_wind,
)


class SpecTableTests(SimpleTestCase):
    """One case per cell in the PDF's risk table, tested through the policy."""

    CASES = {
        "wind": [(10, LEVEL_LOW), (30, LEVEL_MODERATE), (40, LEVEL_HIGH), (50, LEVEL_SEVERE), (60, LEVEL_NO_TRAVEL)],
        "rain": [(0.05, LEVEL_LOW), (0.2, LEVEL_MODERATE), (0.3, LEVEL_HIGH), (0.75, LEVEL_SEVERE), (1.5, LEVEL_NO_TRAVEL)],
        "snow": [(0.2, LEVEL_LOW), (0.7, LEVEL_MODERATE), (1.5, LEVEL_HIGH), (2.5, LEVEL_SEVERE), (3.5, LEVEL_NO_TRAVEL)],
    }

    def test_every_band_of_every_hazard(self):
        for hazard, cases in self.CASES.items():
            for reading, expected in cases:
                with self.subTest(hazard=hazard, reading=reading):
                    self.assertEqual(DEFAULT_POLICY.classify_hazard(hazard, reading, 0), expected)

    def test_load_rules_from_the_sheet(self):
        # >=55 mph -> No Travel for any load
        for load in (0, 20_000, 45_000):
            self.assertEqual(classify_wind(55, load), LEVEL_NO_TRAVEL)
        # 45-54 mph + >30,000 lb -> No Travel
        self.assertEqual(classify_wind(45, 30_001), LEVEL_NO_TRAVEL)
        self.assertEqual(classify_wind(54.9, 45_000), LEVEL_NO_TRAVEL)
        self.assertEqual(classify_wind(54.9, 30_000), LEVEL_SEVERE)
        # 35-44 mph + >40,000 lb -> Severe
        self.assertEqual(classify_wind(35, 40_001), LEVEL_SEVERE)
        self.assertEqual(classify_wind(44.9, 40_001), LEVEL_SEVERE)
        self.assertEqual(classify_wind(44.9, 35_000), LEVEL_HIGH)
        # load rules never touch light wind
        self.assertEqual(classify_wind(34.9, 80_000), LEVEL_MODERATE)

    def test_load_rules_do_not_affect_rain_or_snow(self):
        self.assertEqual(DEFAULT_POLICY.classify_hazard("rain", 0.3, 80_000), LEVEL_HIGH)
        self.assertEqual(DEFAULT_POLICY.classify_hazard("snow", 1.5, 80_000), LEVEL_HIGH)

    def test_heavy_load_wind_can_drive_the_checkpoint(self):
        level, drivers = DEFAULT_POLICY.classify({"wind": 40, "rain": 0.3, "snow": 0}, 45_000)
        self.assertEqual(level, LEVEL_SEVERE)
        self.assertEqual(drivers, ["wind"])


class ExtensionTests(SimpleTestCase):
    def test_a_new_hazard_is_one_entry(self):
        ice = Hazard(
            name="ice",
            field="ice_in_hr",
            digits=2,
            thresholds=(Threshold(LEVEL_NO_TRAVEL, 0.25), Threshold(LEVEL_HIGH, 0.05)),
        )
        policy = RiskPolicy(hazards=HAZARDS + (ice,))
        level, drivers = policy.classify({"wind": 5, "rain": 0, "snow": 0, "ice": 0.1}, 10_000)
        self.assertEqual((level, drivers), (LEVEL_HIGH, ["ice"]))
        level, _ = policy.classify({"wind": 5, "ice": 0.3}, 10_000)
        self.assertEqual(level, LEVEL_NO_TRAVEL)
        # the default policy ignores readings it has no hazard for
        self.assertEqual(DEFAULT_POLICY.classify({"wind": 5, "ice": 0.3}, 10_000)[0], LEVEL_LOW)

    def test_a_new_load_rule_is_one_entry(self):
        snow_rule = LoadRule("snow", 1.0, 2.0, over_lb=50_000, level=LEVEL_SEVERE)
        policy = RiskPolicy(load_rules=LOAD_RULES + (snow_rule,))
        self.assertEqual(policy.classify_hazard("snow", 1.5, 60_000), LEVEL_SEVERE)
        self.assertEqual(policy.classify_hazard("snow", 1.5, 50_000), LEVEL_HIGH)
        self.assertEqual(policy.classify_hazard("snow", 2.5, 60_000), LEVEL_SEVERE)

    def test_load_rule_never_lowers_a_level(self):
        soft = LoadRule("wind", 55, None, over_lb=0, level=LEVEL_MODERATE)
        policy = RiskPolicy(load_rules=(soft,))
        self.assertEqual(policy.classify_hazard("wind", 60, 10_000), LEVEL_NO_TRAVEL)

    def test_ranking_order_is_configurable(self):
        fast = {"name": "fast", "duration_seconds": 100, "stats": {"severe_miles": 10}}
        safe = {"name": "safe", "duration_seconds": 900, "stats": {"severe_miles": 0}}
        self.assertEqual(DEFAULT_POLICY.rank([fast, safe])[0]["name"], "safe")
        time_first = RiskPolicy(ranking=(RankCriterion("duration", route_key="duration_seconds"),) + RANKING)
        self.assertEqual(time_first.rank([safe, fast])[0]["name"], "fast")

    def test_missing_average_ranks_last(self):
        known = {"name": "known", "duration_seconds": 900, "stats": {"avg_risk": 4}}
        unknown = {"name": "unknown", "duration_seconds": 1, "stats": {"avg_risk": None}}
        policy = RiskPolicy(ranking=(RankCriterion("avg_risk", stat="avg_risk"),))
        self.assertEqual(policy.rank([unknown, known])[0]["name"], "known")
