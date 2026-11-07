"""Weather risk rules for truck routing.

Everything that decides a risk level is data, not code:

* ``HAZARDS`` holds one ``Hazard`` per weather reading (wind, rain, snow).
  Each hazard lists its thresholds from most to least severe.
* ``LOAD_RULES`` raises a hazard's level for heavy loads.
* ``RANKING`` is the ordered list of tie-breakers used to recommend a route.

These are bundled in a ``RiskPolicy``. To add a hazard (for example ice or
visibility), add a ``Hazard`` to the policy and a matching ``WeatherVariable``
in ``weather.py``. To add a load rule or change the ranking, edit the tuples
below or build a new ``RiskPolicy`` and pass it to ``build_plan``.

Boundary handling follows the assessment sheet: a value on a shared boundary
goes to the more severe band, except where the sheet uses a strict ``>``
(rain > 1.00 and snow > 3.0 are No Travel; exactly 1.00 / 3.0 is Severe).
A checkpoint takes the worst level across all hazards.
"""

from dataclasses import dataclass, field

LEVEL_LOW = "low"
LEVEL_MODERATE = "moderate"
LEVEL_HIGH = "high"
LEVEL_SEVERE = "severe"
LEVEL_NO_TRAVEL = "no_travel"

LEVELS = (LEVEL_LOW, LEVEL_MODERATE, LEVEL_HIGH, LEVEL_SEVERE, LEVEL_NO_TRAVEL)
SCORES = {level: index for index, level in enumerate(LEVELS, start=1)}
MILE_KEYS = {level: f"{level}_miles" for level in LEVELS}


@dataclass(frozen=True)
class Threshold:
    """``level`` applies when the reading is at (or strictly above) ``value``."""

    level: str
    value: float
    strict: bool = False

    def matches(self, reading):
        return reading > self.value if self.strict else reading >= self.value


@dataclass(frozen=True)
class Hazard:
    """One weather reading and its bands.

    ``thresholds`` is ordered most severe first. A reading that matches none
    of them is ``default``. ``field`` and ``digits`` control how the reading
    is reported on each checkpoint in the API response.
    """

    name: str
    field: str
    digits: int
    thresholds: tuple
    default: str = LEVEL_LOW

    def classify(self, reading):
        for threshold in self.thresholds:
            if threshold.matches(reading):
                return threshold.level
        return self.default


@dataclass(frozen=True)
class LoadRule:
    """Raise ``hazard`` to ``level`` when the reading is in ``[min_value, max_value)``
    and the load is strictly greater than ``over_lb``. ``max_value=None`` means no upper bound.
    """

    hazard: str
    min_value: float
    max_value: float | None
    over_lb: float
    level: str

    def applies(self, hazard, reading, load_lb):
        if hazard != self.hazard or load_lb <= self.over_lb or reading < self.min_value:
            return False
        return self.max_value is None or reading < self.max_value


@dataclass(frozen=True)
class RankCriterion:
    """One tie-breaker. Lower values rank first. ``None`` sorts last."""

    name: str
    stat: str | None = None
    route_key: str | None = None

    def value(self, route):
        raw = route["stats"].get(self.stat) if self.stat else route.get(self.route_key)
        return float("inf") if raw is None else raw


WIND = Hazard(
    name="wind",
    field="wind_mph",
    digits=1,
    thresholds=(
        Threshold(LEVEL_NO_TRAVEL, 55),
        Threshold(LEVEL_SEVERE, 45),
        Threshold(LEVEL_HIGH, 35),
        Threshold(LEVEL_MODERATE, 25),
    ),
)

RAIN = Hazard(
    name="rain",
    field="rain_in_hr",
    digits=3,
    thresholds=(
        Threshold(LEVEL_NO_TRAVEL, 1.00, strict=True),
        Threshold(LEVEL_SEVERE, 0.50),
        Threshold(LEVEL_HIGH, 0.25),
        Threshold(LEVEL_MODERATE, 0.10),
    ),
)

SNOW = Hazard(
    name="snow",
    field="snow_in_hr",
    digits=3,
    thresholds=(
        Threshold(LEVEL_NO_TRAVEL, 3.0, strict=True),
        Threshold(LEVEL_SEVERE, 2.0),
        Threshold(LEVEL_HIGH, 1.0),
        Threshold(LEVEL_MODERATE, 0.5),
    ),
)

HAZARDS = (WIND, RAIN, SNOW)

# >=55 mph is No Travel for any load; that is already WIND's top band.
LOAD_RULES = (
    LoadRule("wind", 45, 55, over_lb=30_000, level=LEVEL_NO_TRAVEL),
    LoadRule("wind", 35, 45, over_lb=40_000, level=LEVEL_SEVERE),
)

# No Travel is ranked ahead of Severe because it is the stricter outcome.
RANKING = (
    RankCriterion("no_travel_miles", stat="no_travel_miles"),
    RankCriterion("severe_miles", stat="severe_miles"),
    RankCriterion("high_miles", stat="high_miles"),
    RankCriterion("avg_risk", stat="avg_risk"),
    RankCriterion("duration", route_key="duration_seconds"),
)


def worse(left, right):
    """The more severe of two levels."""
    return left if SCORES[left] >= SCORES[right] else right


@dataclass(frozen=True)
class RiskPolicy:
    hazards: tuple = HAZARDS
    load_rules: tuple = LOAD_RULES
    ranking: tuple = RANKING
    _by_name: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_by_name", {hazard.name: hazard for hazard in self.hazards})

    def hazard(self, name):
        return self._by_name[name]

    def classify_hazard(self, name, reading, load_lb):
        level = self.hazard(name).classify(reading)
        for rule in self.load_rules:
            if rule.applies(name, reading, load_lb):
                level = worse(level, rule.level)
        return level

    def classify(self, readings, load_lb):
        """Return (level, drivers) for a mapping of hazard name to reading.

        Missing (``None``) readings are ignored. ``drivers`` lists every
        hazard that ties for the worst level, in policy order. If every
        reading is missing, level is ``None``.
        """
        scored = []
        for hazard in self.hazards:
            reading = readings.get(hazard.name)
            if reading is not None:
                scored.append((hazard.name, self.classify_hazard(hazard.name, reading, load_lb)))
        if not scored:
            return None, []
        worst = max(SCORES[level] for _, level in scored)
        drivers = [name for name, level in scored if SCORES[level] == worst]
        return LEVELS[worst - 1], drivers

    def rank(self, routes):
        return sorted(routes, key=lambda route: tuple(c.value(route) for c in self.ranking))


DEFAULT_POLICY = RiskPolicy()


# Convenience wrappers around the default policy.

def classify_wind(mph, load_lb):
    return DEFAULT_POLICY.classify_hazard("wind", mph, load_lb)


def classify_rain(inches_per_hour):
    return DEFAULT_POLICY.classify_hazard("rain", inches_per_hour, 0)


def classify_snow(inches_per_hour):
    return DEFAULT_POLICY.classify_hazard("snow", inches_per_hour, 0)


def classify_checkpoint(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    return DEFAULT_POLICY.classify({"wind": wind_mph, "rain": rain_in_hr, "snow": snow_in_hr}, load_lb)


def rank_routes(routes, policy=DEFAULT_POLICY):
    """Best route first: No Travel, Severe, High, average risk, then time."""
    return policy.rank(routes)


def segment_stats(checkpoints):
    """Mile totals for the segment that ends at each checkpoint after the first.

    The ending checkpoint's level owns that segment. Unknown levels are left
    out of both the buckets and the average.
    """
    totals = {key: 0.0 for key in MILE_KEYS.values()}
    weighted = 0.0
    counted = 0.0
    for previous, current in zip(checkpoints, checkpoints[1:]):
        miles = current["mile"] - previous["mile"]
        if miles <= 0:
            continue
        level = current.get("level")
        if level not in SCORES:
            continue
        totals[MILE_KEYS[level]] += miles
        weighted += SCORES[level] * miles
        counted += miles
    average = (weighted / counted) if counted else None
    stats = {key: round(value, 3) for key, value in totals.items()}
    stats["avg_risk"] = round(average, 3) if average is not None else None
    return stats
