import { formatHour } from "../format";

const METRICS = [
  ["risk", "Risk"],
  ["wind", "Wind"],
  ["rain", "Rain"],
  ["snow", "Snow"],
];

export default function ForecastBar({ departure, hour, metric, onHour, onMetric, disabled }) {
  return (
    <div className="forecast">
      <div className="metrics" role="group" aria-label="Heatmap metric">
        {METRICS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={metric === id ? "on" : ""}
            onClick={() => onMetric(id)}
            disabled={disabled}
          >
            {label}
          </button>
        ))}
      </div>
      <label className="slider">
        <span>{disabled ? "Hour 0" : formatHour(departure, hour)}</span>
        <input
          type="range"
          min="0"
          max="48"
          step="1"
          value={hour}
          disabled={disabled}
          aria-label="Forecast hour from departure"
          onChange={(event) => onHour(Number(event.target.value))}
        />
        <span>48h</span>
      </label>
      <p>Corridor forecast from departure. Checkpoints use the truck&apos;s arrival time.</p>
    </div>
  );
}
