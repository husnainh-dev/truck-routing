import { useState } from "react";
import { planTrip } from "./api";
import ForecastBar from "./components/ForecastBar";
import MapView from "./components/MapView";
import RoutePanel from "./components/RoutePanel";
import TripForm from "./components/TripForm";
import { LEVEL_COLORS, LEVEL_LABELS } from "./format";

const LEGEND = ["low", "moderate", "high", "severe", "no_travel"];

export default function App() {
  const [draft, setDraft] = useState({
    origin: "",
    origin_place: null,
    destination: "",
    destination_place: null,
    departure: "",
    load_weight_lb: "",
    sample_miles: "25",
  });
  const [plan, setPlan] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [hour, setHour] = useState(0);
  const [metric, setMetric] = useState("risk");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await planTrip({
        origin: draft.origin,
        destination: draft.destination,
        origin_place: draft.origin_place,
        destination_place: draft.destination_place,
        departure: new Date(draft.departure).toISOString(),
        load_weight_lb: Number(draft.load_weight_lb),
        sample_miles: Number(draft.sample_miles),
      });
      const recommended = result.routes.find((route) => route.recommended) || result.routes[0];
      setPlan(result);
      setSelectedId(recommended ? recommended.id : null);
      setHour(0);
    } catch (err) {
      setError(err.message || "The trip could not be planned.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="shell">
      <aside className="panel">
        <header className="brand">
          <p className="eyebrow">Truck desk</p>
          <h1>Weather-aware routing</h1>
          <p>Three corridors, scored for wind, rain, snow, and the load on the truck.</p>
        </header>
        <TripForm draft={draft} onChange={setDraft} onSubmit={onSubmit} loading={loading} />
        {error && <p className="warn">{error}</p>}
        <div className="panel-scroll">
          <RoutePanel plan={plan} selectedId={selectedId} onSelect={setSelectedId} />
        </div>
      </aside>
      <main className="map-wrap">
        <MapView
          plan={plan}
          places={{ origin: draft.origin_place, destination: draft.destination_place }}
          selectedId={selectedId}
          hour={hour}
          metric={metric}
          onSelect={setSelectedId}
        />
        <ul className="legend">
          {LEGEND.map((level) => (
            <li key={level}>
              <span style={{ background: LEVEL_COLORS[level] }} />
              {LEVEL_LABELS[level]}
            </li>
          ))}
        </ul>
        {loading && <p className="map-status">Sampling weather along the routes…</p>}
        <ForecastBar
          departure={plan?.departure}
          hour={hour}
          metric={metric}
          onHour={setHour}
          onMetric={setMetric}
          disabled={!plan || loading}
        />
      </main>
    </div>
  );
}
