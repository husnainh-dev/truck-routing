from datetime import datetime, timedelta, timezone

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings
from django.http import JsonResponse

from .services.errors import GeocodeError, RoutingError, WeatherError
from .services.plan import build_plan

SAMPLE_MILES = {10, 25, 50}


def health(_request):
    """Liveness check for the hosting platform."""
    return JsonResponse({"ok": True})


class PlanView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            origin = _text(request.data, "origin")
            destination = _text(request.data, "destination")
            departure = _departure(request.data.get("departure"))
            load_lb = _load(request.data.get("load_weight_lb"))
            sample_miles = _sample(request.data.get("sample_miles", 25))
            origin_place = _place(request.data.get("origin_place"), origin)
            destination_place = _place(request.data.get("destination_place"), destination)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)

        now = datetime.now(timezone.utc)
        if departure < now - timedelta(hours=1) or departure > now + timedelta(days=7):
            return Response(
                {"error": "Departure must be between now and 7 days from now."},
                status=400,
            )

        try:
            result = build_plan(
                origin,
                destination,
                departure,
                load_lb,
                sample_miles,
                settings.ORS_API_KEY,
                origin_place=origin_place,
                destination_place=destination_place,
            )
        except GeocodeError as exc:
            return Response({"error": str(exc)}, status=400)
        except (RoutingError, WeatherError) as exc:
            return Response({"error": str(exc)}, status=502)
        return Response(result)


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        label = "Origin" if key == "origin" else "Destination"
        raise ValueError(f"{label} is required.")
    return value.strip()


def _departure(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Departure date and time are required.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Departure must be an ISO date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load(value):
    if value is None or value == "":
        raise ValueError("Load weight is required.")
    try:
        load = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Load weight must be a number.") from exc
    if load < 0 or load > 200_000:
        raise ValueError("Load weight must be between 0 and 200,000 lb.")
    return load


def _sample(value):
    try:
        sample = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sample interval must be 10, 25, or 50 miles.") from exc
    if sample not in SAMPLE_MILES:
        raise ValueError("Sample interval must be 10, 25, or 50 miles.")
    return sample


def _place(value, label):
    """Optional ``{lat, lon, label?}`` picked in the browser. ``None`` means geocode the text."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("Place must be an object with lat and lon.")
    try:
        lat = float(value["lat"])
        lon = float(value["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Place must have a numeric lat and lon.") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Place coordinates are out of range.")
    text = value.get("label")
    if not isinstance(text, str) or not text.strip():
        text = label
    return {"label": text.strip(), "lat": lat, "lon": lon}
