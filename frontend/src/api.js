export async function planTrip(body) {
  const response = await fetch("/api/plan/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "The trip could not be planned.");
  }
  return data;
}
