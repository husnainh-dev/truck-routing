from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from planner.services import weather
from planner.services.errors import WeatherError
from planner.services.weather import (
    Forecast,
    _nearest_index,
    _scale,
    cell_key,
    fetch_forecasts,
    forecast_days_needed,
)

T0 = datetime(2026, 11, 5, 0, tzinfo=timezone.utc)


def _payload(hours=3, wind_unit="mp/h", snow_unit="inch", snow=0.5):
    return {
        "hourly": {
            "time": [(T0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)],
            "wind_speed_10m": [10.0 + h for h in range(hours)],
            "rain": [0.0] * hours,
            "snowfall": [snow] * hours,
        },
        "hourly_units": {"wind_speed_10m": wind_unit, "rain": "inch", "snowfall": snow_unit},
    }


def _rows(count, hours=3):
    return [_payload(hours) for _ in range(count)]


def _response(payload, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    return response


class UnitTests(SimpleTestCase):
    def test_snow_in_cm_is_converted_to_inches(self):
        self.assertAlmostEqual(_scale([2.54], "cm", weather.SNOW_TO_INCH)[0], 1.0)

    def test_wind_in_kmh_is_converted_to_mph(self):
        self.assertAlmostEqual(_scale([100], "km/h", weather.WIND_TO_MPH)[0], 62.1371, places=3)

    def test_missing_values_stay_missing(self):
        self.assertEqual(_scale([None, 1], "inch", weather.RAIN_TO_INCH), [None, 1.0])

    def test_unknown_unit_is_an_error(self):
        with self.assertRaises(WeatherError):
            _scale([1], "furlongs", weather.RAIN_TO_INCH)


class NearestHourTests(SimpleTestCase):
    times = [T0 + timedelta(hours=h) for h in range(5)]

    def test_rounds_to_the_nearest_hour(self):
        self.assertEqual(_nearest_index(self.times, T0 + timedelta(minutes=29)), 0)
        self.assertEqual(_nearest_index(self.times, T0 + timedelta(minutes=31)), 1)
        self.assertEqual(_nearest_index(self.times, T0 + timedelta(hours=3, minutes=50)), 4)

    def test_outside_the_forecast_window_is_unknown(self):
        self.assertIsNone(_nearest_index(self.times, T0 - timedelta(hours=3)))
        self.assertIsNone(_nearest_index(self.times, T0 + timedelta(hours=7)))

    def test_naive_times_are_treated_as_utc(self):
        self.assertEqual(_nearest_index(self.times, datetime(2026, 11, 5, 2)), 2)


@mock.patch.object(weather.time, "sleep")
class FetchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch.object(weather.requests, "get")
    def test_batched_points_share_one_request_and_cell(self, get, _sleep):
        get.return_value = _response([_payload(), _payload()])
        forecast = fetch_forecasts([(41.0, -87.0), (41.001, -87.001), (42.0, -88.0)])
        self.assertEqual(get.call_count, 1)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["hourly"], "wind_speed_10m,rain,snowfall")
        self.assertEqual(params["wind_speed_unit"], "mph")
        readings = forecast.readings(41.0, -87.0, T0 + timedelta(hours=1))
        self.assertEqual(readings, {"wind": 11.0, "rain": 0.0, "snow": 0.5})
        self.assertEqual(forecast.at(41.0, -87.0, T0 + timedelta(hours=1)), (11.0, 0.0, 0.5))

    @mock.patch.object(weather.requests, "get")
    def test_cm_snowfall_in_a_response_is_converted(self, get, _sleep):
        get.return_value = _response(_payload(snow_unit="cm", snow=2.54))
        forecast = fetch_forecasts([(41.0, -87.0)])
        self.assertAlmostEqual(forecast.readings(41.0, -87.0, T0)["snow"], 1.0)

    @mock.patch.object(weather.requests, "get")
    def test_rate_limit_and_bad_shapes_raise(self, get, _sleep):
        get.return_value = _response({}, status=429)
        with self.assertRaises(WeatherError):
            fetch_forecasts([(41.0, -87.0)])
        get.return_value = _response([_payload()])
        with self.assertRaises(WeatherError):
            fetch_forecasts([(41.0, -87.0), (42.0, -88.0)])

    def test_unknown_cell_reads_as_missing(self, _sleep):
        forecast = Forecast({})
        self.assertEqual(forecast.readings(1, 2, T0), {"wind": None, "rain": None, "snow": None})

    def test_cell_key_rounds_to_two_places(self, _sleep):
        self.assertEqual(cell_key(41.12345, -87.98765), (41.12, -87.99))


UNTIL = T0 + timedelta(hours=2)  # inside the 3-hour test payload


@mock.patch.object(weather.time, "sleep")
@mock.patch.object(weather.requests, "get")
class CacheAndLimitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_second_plan_for_the_same_cells_uses_the_cache(self, get, _sleep):
        get.return_value = _response(_rows(2))
        points = [(41.0, -87.0), (42.0, -88.0)]
        fetch_forecasts(points, until=UNTIL, now=T0)
        forecast = fetch_forecasts(points, until=UNTIL, now=T0)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(forecast.readings(42.0, -88.0, T0)["wind"], 10.0)

    def test_only_uncached_cells_are_requested(self, get, _sleep):
        get.return_value = _response(_payload())
        fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        fetch_forecasts([(41.0, -87.0), (42.0, -88.0)], until=UNTIL, now=T0)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"]["latitude"], "42.0")

    def test_cache_that_does_not_reach_until_is_refetched(self, get, _sleep):
        get.return_value = _response(_payload(hours=3))
        fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        get.return_value = _response(_payload(hours=10))
        fetch_forecasts([(41.0, -87.0)], until=T0 + timedelta(hours=8), now=T0)
        self.assertEqual(get.call_count, 2)

    def test_only_the_needed_forecast_days_are_requested(self, get, _sleep):
        get.return_value = _response(_payload())
        fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        self.assertEqual(get.call_args.kwargs["params"]["forecast_days"], 1)

    def test_burst_429_is_retried(self, get, sleep):
        busy = _response({"error": True, "reason": "Minutely API request limit exceeded."}, status=429)
        get.side_effect = [busy, _response(_payload())]
        forecast = fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(weather.RETRY_DELAYS_SECONDS[0])
        self.assertEqual(forecast.readings(41.0, -87.0, T0)["wind"], 10.0)

    def test_persistent_429_gives_up_after_the_retries(self, get, sleep):
        get.return_value = _response({"reason": "Too many concurrent requests"}, status=429)
        with self.assertRaisesRegex(WeatherError, "busy"):
            fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        self.assertEqual(get.call_count, len(weather.RETRY_DELAYS_SECONDS) + 1)

    def test_hourly_or_daily_limit_fails_fast_with_a_clear_message(self, get, sleep):
        for reason in ("Hourly API request limit exceeded.", "Daily API request limit exceeded."):
            with self.subTest(reason=reason):
                get.reset_mock()
                get.return_value = _response({"reason": reason}, status=429)
                with self.assertRaisesRegex(WeatherError, "limit for this hour or day"):
                    fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
                self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()


class ForecastDaysTests(SimpleTestCase):
    def test_days_cover_until_plus_an_hour(self):
        now = datetime(2026, 11, 5, 15, tzinfo=timezone.utc)
        self.assertEqual(forecast_days_needed(datetime(2026, 11, 5, 20, tzinfo=timezone.utc), now), 1)
        self.assertEqual(forecast_days_needed(datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc), now), 2)
        # departure 7 days out + 48 h heatmap -> 10 days, not 16
        self.assertEqual(forecast_days_needed(now + timedelta(days=7, hours=48), now), 10)
        self.assertEqual(forecast_days_needed(now + timedelta(days=40), now), 16)
        self.assertEqual(forecast_days_needed(None, now), 16)


@mock.patch.object(weather.requests, "get")
class EndpointTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _fetch(self, get):
        get.return_value = _response(_payload())
        fetch_forecasts([(41.0, -87.0)], until=UNTIL, now=T0)
        return get.call_args.args[0], get.call_args.kwargs["params"]

    @override_settings(OPEN_METEO_API_KEY="", OPEN_METEO_URL="")
    def test_free_api_by_default(self, get):
        url, params = self._fetch(get)
        self.assertEqual(url, weather.FORECAST_URL)
        self.assertNotIn("apikey", params)

    @override_settings(OPEN_METEO_API_KEY="paid", OPEN_METEO_URL="")
    def test_key_switches_to_the_customer_api(self, get):
        url, params = self._fetch(get)
        self.assertEqual(url, weather.CUSTOMER_FORECAST_URL)
        self.assertEqual(params["apikey"], "paid")

    @override_settings(OPEN_METEO_API_KEY="", OPEN_METEO_URL="http://localhost:8080/v1/forecast")
    def test_self_hosted_url(self, get):
        url, params = self._fetch(get)
        self.assertEqual(url, "http://localhost:8080/v1/forecast")
        self.assertNotIn("apikey", params)
