import { useEffect, useRef } from "react";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

maplibregl.setWorkerUrl(maplibreWorkerUrl);
import { LEVEL_COLORS, LEVEL_LABELS, escapeHtml, formatClock, formatReading } from "../format";

const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const EMPTY = { type: "FeatureCollection", features: [] };

const METRIC_MAX = { wind: 55, rain: 1, snow: 3 };
const METRIC_COLORS = {
  wind: ["rgba(186, 214, 224, 0.15)", "rgba(18, 78, 148, 0.9)"],
  rain: ["rgba(198, 222, 240, 0.15)", "rgba(14, 78, 168, 0.9)"],
  snow: ["rgba(226, 236, 244, 0.2)", "rgba(62, 104, 176, 0.9)"],
};

const LINE_COLOR = [
  "match",
  ["get", "level"],
  "low", LEVEL_COLORS.low,
  "moderate", LEVEL_COLORS.moderate,
  "high", LEVEL_COLORS.high,
  "severe", LEVEL_COLORS.severe,
  "no_travel", LEVEL_COLORS.no_travel,
  LEVEL_COLORS.unknown,
];

export default function MapView({ plan, places, selectedId, hour, metric, onSelect }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const popupRef = useRef(null);
  const heatPlanRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  const planRef = useRef(plan);
  const selectedRef = useRef(selectedId);
  const hourRef = useRef(hour);
  const metricRef = useRef(metric);
  const fittedRef = useRef(null);
  const syncHeatRef = useRef(() => {});
  const markersRef = useRef({});
  const pinFitRef = useRef("");

  // A freshly picked place wins over the planned end, so pins move as soon as
  // the driver picks a suggestion.
  const originPin = places?.origin || plan?.origin || null;
  const destinationPin = places?.destination || plan?.destination || null;
  const pinKey = [originPin, destinationPin]
    .map((pin) => (pin ? `${pin.lat},${pin.lon}` : ""))
    .join("|");

  onSelectRef.current = onSelect;
  planRef.current = plan;
  selectedRef.current = selectedId;
  hourRef.current = hour;
  metricRef.current = metric;
  syncHeatRef.current = () => {
    const map = mapRef.current;
    if (!map?.getLayer("heat")) return;
    const currentPlan = planRef.current;
    if (heatPlanRef.current !== currentPlan) {
      heatPlanRef.current = currentPlan;
      map.getSource("heat").setData(heatCollection(currentPlan));
      map.setPaintProperty("heat", "circle-radius", heatRadius(currentPlan));
    }
    const hourNow = hourRef.current;
    const metricNow = metricRef.current;
    map.setPaintProperty("heat", "circle-color", heatColor(hourNow, metricNow));
    map.setPaintProperty("heat", "circle-opacity", heatOpacity(hourNow, metricNow));
  };

  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE,
      center: [-96, 38.5],
      zoom: 3.4,
      attributionControl: true,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" });
    mapRef.current = map;
    popupRef.current = popup;

    map.on("load", () => {
      map.addSource("heat", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "heat",
        type: "circle",
        source: "heat",
        paint: {
          "circle-radius": heatRadius(null),
          "circle-color": heatColor(0, "risk"),
          "circle-opacity": 0.62,
          "circle-blur": 0.65,
        },
      });
      map.addSource("routes", { type: "geojson", data: EMPTY });
      map.addSource("checkpoints", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "route-casing",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#14222b",
          "line-width": ["case", ["==", ["get", "selected"], 1], 10, 5.5],
          "line-opacity": ["case", ["==", ["get", "selected"], 1], 0.8, 0.22],
        },
      });
      map.addLayer({
        id: "route-lines",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": LINE_COLOR,
          "line-width": ["case", ["==", ["get", "selected"], 1], 6, 3],
          "line-opacity": ["case", ["==", ["get", "selected"], 1], 0.96, 0.42],
        },
      });
      map.addLayer({
        id: "checkpoints",
        type: "circle",
        source: "checkpoints",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 7, 8, 10, 12, 14],
          "circle-color": LINE_COLOR,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": ["case", ["==", ["get", "selected"], 1], 1, 0.7],
        },
      });
      map.on("click", (event) => {
        const pad = 12;
        const area = [
          [event.point.x - pad, event.point.y - pad],
          [event.point.x + pad, event.point.y + pad],
        ];
        const checkpoints = map.queryRenderedFeatures(area, { layers: ["checkpoints"] });
        if (checkpoints.length) {
          const properties = checkpoints[0].properties;
          onSelectRef.current(properties.routeId);
          popup
            .setLngLat(checkpoints[0].geometry.coordinates)
            .setHTML(popupHtml(properties))
            .addTo(map);
          return;
        }
        const lines = map.queryRenderedFeatures(event.point, { layers: ["route-lines"] });
        if (lines.length) {
          onSelectRef.current(lines[0].properties.routeId);
          popup.remove();
        }
      });
      map.on("mouseenter", "route-lines", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "route-lines", () => {
        map.getCanvas().style.cursor = "";
      });
      map.on("mouseenter", "checkpoints", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "checkpoints", () => {
        map.getCanvas().style.cursor = "";
      });
      drawRoutes(map, planRef.current, selectedRef.current, fittedRef);
      syncHeatRef.current();
    });

    return () => {
      Object.values(markersRef.current).forEach((marker) => marker.remove());
      markersRef.current = {};
      popup.remove();
      map.remove();
      mapRef.current = null;
      heatPlanRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getSource("routes")) return;
    drawRoutes(map, plan, selectedId, fittedRef);
  }, [plan, selectedId]);

  useEffect(() => {
    syncHeatRef.current();
  }, [plan, hour, metric]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const pins = { origin: originPin, destination: destinationPin };
    Object.entries(pins).forEach(([kind, pin]) => {
      const existing = markersRef.current[kind];
      if (!pin) {
        existing?.remove();
        delete markersRef.current[kind];
        return;
      }
      // MapLibre needs a position before addTo(), or it throws.
      const marker = existing || createPin(kind);
      marker.setLngLat([pin.lon, pin.lat]);
      if (!existing) marker.addTo(map);
      marker.getPopup().setHTML(pinPopupHtml(kind, pin.label));
      markersRef.current[kind] = marker;
    });
    // Before a plan exists, frame the picked pins. After, drawRoutes frames the routes.
    if (plan || pinKey === pinFitRef.current) return;
    pinFitRef.current = pinKey;
    const present = [originPin, destinationPin].filter(Boolean);
    if (present.length === 1) {
      map.flyTo({ center: [present[0].lon, present[0].lat], zoom: Math.max(map.getZoom(), 7), duration: 700 });
    } else if (present.length === 2) {
      const bounds = new maplibregl.LngLatBounds();
      present.forEach((pin) => bounds.extend([pin.lon, pin.lat]));
      map.fitBounds(bounds, { padding: fitPadding(map), maxZoom: 9, duration: 700 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinKey, plan]);

  return <div ref={containerRef} className="map" />;
}

function drawRoutes(map, plan, selectedId, fittedRef) {
  const routes = map.getSource("routes");
  const checkpoints = map.getSource("checkpoints");
  if (!routes || !checkpoints) return;
  routes.setData(routeCollection(plan, selectedId));
  checkpoints.setData(checkpointCollection(plan, selectedId));
  if (!plan || fittedRef.current === plan) return;
  const bounds = new maplibregl.LngLatBounds();
  plan.routes.forEach((route) => {
    route.geometry.coordinates.forEach((position) => bounds.extend(position));
  });
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: fitPadding(map), duration: 700 });
    fittedRef.current = plan;
  }
}

// Padding that never exceeds the canvas. Oversized padding on a narrow map
// makes fitBounds fail and leaves the camera somewhere unrelated.
function fitPadding(map) {
  const canvas = map.getCanvas();
  const width = canvas.clientWidth || 0;
  const height = canvas.clientHeight || 0;
  const side = Math.min(48, Math.floor(width * 0.12));
  const top = Math.min(48, Math.floor(height * 0.1));
  const bottom = Math.min(130, Math.floor(height * 0.3));
  return { top, right: side, bottom, left: side };
}

function createPin(kind) {
  const element = document.createElement("div");
  element.className = `pin pin-${kind}`;
  // MapLibre owns the marker element's transform, so the rotated teardrop is an inner node.
  element.innerHTML = `<div class="pin-shape"><span>${kind === "origin" ? "A" : "B"}</span></div>`;
  element.setAttribute("aria-label", kind === "origin" ? "Origin" : "Destination");
  return new maplibregl.Marker({ element, anchor: "bottom" }).setPopup(
    new maplibregl.Popup({ offset: 30, closeButton: false, maxWidth: "240px" }),
  );
}

function pinPopupHtml(kind, label) {
  return `
    <div class="popup">
      <strong>${kind === "origin" ? "Origin" : "Destination"}</strong>
      <p>${escapeHtml(label || "")}</p>
    </div>
  `;
}

function routeCollection(plan, selectedId) {
  if (!plan) return EMPTY;
  const ordered = [...plan.routes].sort((left, right) => {
    if (left.id === selectedId) return 1;
    if (right.id === selectedId) return -1;
    return 0;
  });
  const features = [];
  ordered.forEach((route) => {
    const selected = route.id === selectedId ? 1 : 0;
    route.segments.forEach((segment) => {
      features.push({
        type: "Feature",
        properties: { level: segment.level || "unknown", routeId: route.id, selected },
        geometry: { type: "LineString", coordinates: segment.coordinates },
      });
    });
  });
  return { type: "FeatureCollection", features };
}

function checkpointCollection(plan, selectedId) {
  if (!plan) return EMPTY;
  const features = [];
  plan.routes.forEach((route) => {
    const selected = route.id === selectedId ? 1 : 0;
    route.checkpoints.forEach((checkpoint) => {
      features.push({
        type: "Feature",
        properties: {
          routeId: route.id,
          selected,
          mile: checkpoint.mile,
          eta: checkpoint.eta,
          level: checkpoint.level || "unknown",
          wind_mph: checkpoint.wind_mph,
          rain_in_hr: checkpoint.rain_in_hr,
          snow_in_hr: checkpoint.snow_in_hr,
          drivers: (checkpoint.drivers || []).join(", "),
        },
        geometry: { type: "Point", coordinates: [checkpoint.lon, checkpoint.lat] },
      });
    });
  });
  return { type: "FeatureCollection", features };
}

function popupHtml(properties) {
  const level = properties.level || "unknown";
  return `
    <div class="popup">
      <div class="popup-top">
        <strong>Mile ${escapeHtml(Number(properties.mile).toFixed(0))}</strong>
        <span class="tag" style="background:${LEVEL_COLORS[level] || LEVEL_COLORS.unknown}">${escapeHtml(LEVEL_LABELS[level] || level)}</span>
      </div>
      <p>ETA ${escapeHtml(formatClock(properties.eta))}</p>
      <p>Wind ${escapeHtml(formatReading(properties.wind_mph, 0, "mph"))}</p>
      <p>Rain ${escapeHtml(formatReading(properties.rain_in_hr, 2, "in/hr"))}</p>
      <p>Snow ${escapeHtml(formatReading(properties.snow_in_hr, 2, "in/hr"))}</p>
      <p>Driven by ${escapeHtml(properties.drivers || "—")}</p>
    </div>
  `;
}

function heatCollection(plan) {
  const features = (plan?.heatmap?.points ?? []).map((point) => ({
    type: "Feature",
    properties: {
      risk: point.risk,
      wind: zeroFill(point.wind),
      rain: zeroFill(point.rain),
      snow: zeroFill(point.snow),
    },
    geometry: { type: "Point", coordinates: [point.lon, point.lat] },
  }));
  return { type: "FeatureCollection", features };
}

// Circle radius in real miles (about 0.75x the grid spacing) so neighbouring
// cells always overlap. MapLibre radii are pixels, so scale with zoom:
// pixels = metres / metresPerPixel, and metresPerPixel halves at each zoom.
const METRES_PER_PIXEL_Z0 = 78271.517; // 512 px world tiles at the equator

function heatRadius(plan) {
  const points = plan?.heatmap?.points ?? [];
  const spacing = Number(plan?.heatmap?.spacing_miles) || 10;
  const lat = points.length ? points.reduce((sum, point) => sum + point.lat, 0) / points.length : 38;
  const metres = spacing * 0.75 * 1609.344;
  const pixelsAtZ0 = metres / (METRES_PER_PIXEL_Z0 * Math.cos((lat * Math.PI) / 180));
  return ["interpolate", ["exponential", 2], ["zoom"], 0, pixelsAtZ0, 22, pixelsAtZ0 * 2 ** 22];
}

function zeroFill(values) {
  return (values || []).map((value) => (value == null ? 0 : value));
}

function heatColor(hour, metric) {
  const sample = ["at", hour, ["get", metric]];
  if (metric === "risk") {
    return ["match", sample, 1, "#1f9d55", 2, "#c89212", 3, "#e36a12", 4, "#d21f35", 5, "#4a1024", "rgba(0,0,0,0)"];
  }
  const [low, high] = METRIC_COLORS[metric];
  return ["interpolate", ["linear"], sample, 0, low, METRIC_MAX[metric], high];
}

function heatOpacity(hour, metric) {
  if (metric === "risk") {
    return ["case", ["==", ["at", hour, ["get", "risk"]], 0], 0, 0.62];
  }
  return 0.55;
}
