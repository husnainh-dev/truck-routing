export const LEVEL_COLORS = {
  low: "#1f9d55",
  moderate: "#c89212",
  high: "#e36a12",
  severe: "#d21f35",
  no_travel: "#4a1024",
  unknown: "#8d99a8",
};

export const LEVEL_LABELS = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
  no_travel: "No travel",
  unknown: "Unknown",
};

const MILE_FIELDS = [
  ["no_travel_miles", "no_travel"],
  ["severe_miles", "severe"],
  ["high_miles", "high"],
  ["moderate_miles", "moderate"],
  ["low_miles", "low"],
];

export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) / 60));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (hours <= 0) return `${minutes}m`;
  return `${hours}h ${minutes}m`;
}

export function formatClock(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatHour(departure, hour) {
  if (!departure) return `Hour ${hour}`;
  const date = new Date(new Date(departure).getTime() + hour * 60 * 60 * 1000);
  if (Number.isNaN(date.getTime())) return `Hour ${hour}`;
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatReading(value, digits, unit) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)} ${unit}`;
}

export function mileParts(stats) {
  const total = MILE_FIELDS.reduce((sum, [key]) => sum + (Number(stats[key]) || 0), 0);
  return {
    total,
    parts: MILE_FIELDS.map(([key, level]) => ({
      level,
      miles: Number(stats[key]) || 0,
      share: total > 0 ? ((Number(stats[key]) || 0) / total) * 100 : 0,
    })),
  };
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}
