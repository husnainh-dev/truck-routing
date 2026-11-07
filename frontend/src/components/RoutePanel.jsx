import { LEVEL_COLORS, LEVEL_LABELS, formatDuration, mileParts } from "../format";

const FACTORS = [
  ["no_travel_miles", "No travel"],
  ["severe_miles", "Severe"],
  ["high_miles", "High"],
];

export default function RoutePanel({ plan, selectedId, onSelect }) {
  if (!plan) {
    return (
      <p className="empty">
        Compare three routes. The recommendation prefers fewer no-travel miles, then fewer
        severe miles, then fewer high miles, then lower average risk, then a shorter drive.
      </p>
    );
  }

  const allBlocked = plan.routes.every((route) => route.stats.no_travel_miles > 0);

  return (
    <div className="results">
      {plan.warning && <p className="note">{plan.warning}</p>}
      {allBlocked && (
        <p className="warn">
          Every route includes a no-travel stretch. The highlighted route is the least bad option.
        </p>
      )}
      <ol className="routes">
        {plan.routes.map((route, index) => {
          const { parts } = mileParts(route.stats);
          const selected = route.id === selectedId;
          return (
            <li key={route.id}>
              <button
                type="button"
                className={selected ? "route-card selected" : "route-card"}
                onClick={() => onSelect(route.id)}
              >
                <header>
                  <strong>Route {String.fromCharCode(65 + index)}</strong>
                  {route.recommended && <span className="badge">Best route</span>}
                </header>
                <p className="summary">
                  <span>{formatDuration(route.duration_seconds)}</span>
                  <span>{route.distance_miles.toFixed(0)} mi</span>
                  <span>Avg risk {route.stats.avg_risk == null ? "—" : route.stats.avg_risk.toFixed(2)}</span>
                </p>
                <dl>
                  {FACTORS.map(([key, label]) => (
                    <div key={key}>
                      <dt>{label}</dt>
                      <dd>{Number(route.stats[key] || 0).toFixed(0)} mi</dd>
                    </div>
                  ))}
                </dl>
                <div className="bar" aria-hidden="true">
                  {parts.map((part) =>
                    part.share > 0 ? (
                      <span
                        key={part.level}
                        style={{ width: `${part.share}%`, background: LEVEL_COLORS[part.level] }}
                        title={`${LEVEL_LABELS[part.level]} ${part.miles.toFixed(0)} mi`}
                      />
                    ) : null,
                  )}
                </div>
                <ul className="mile-breakdown">
                  {parts
                    .filter((part) => part.miles >= 0.5)
                    .map((part) => (
                      <li key={part.level}>
                        <span className="dot" style={{ background: LEVEL_COLORS[part.level] }} />
                        {LEVEL_LABELS[part.level]} {part.miles.toFixed(0)} mi
                      </li>
                    ))}
                </ul>
                {route.stats.no_travel_miles + route.stats.severe_miles + route.stats.high_miles < 0.5 &&
                  route.stats.avg_risk != null && (
                    <p className="clear">No high, severe, or no-travel weather on this route.</p>
                  )}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
