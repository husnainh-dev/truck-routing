import PlaceInput from "./PlaceInput";

export default function TripForm({ draft, onChange, onSubmit, loading }) {
  // Functional updates so an async place pick never overwrites newer typing.
  function setField(name, value) {
    onChange((current) => ({ ...current, [name]: value }));
  }

  function update(event) {
    setField(event.target.name, event.target.value);
  }

  return (
    <form className="trip-form" onSubmit={onSubmit}>
      <PlaceInput
        name="origin"
        label="Origin"
        value={draft.origin}
        place={draft.origin_place}
        placeholder="Chicago, IL"
        onText={(text) => setField("origin", text)}
        onPlace={(place) => setField("origin_place", place)}
      />
      <PlaceInput
        name="destination"
        label="Destination"
        value={draft.destination}
        place={draft.destination_place}
        placeholder="St. Louis, MO"
        onText={(text) => setField("destination", text)}
        onPlace={(place) => setField("destination_place", place)}
      />
      <label>
        Departure
        <input
          name="departure"
          type="datetime-local"
          value={draft.departure}
          onChange={update}
          required
        />
      </label>
      <div className="split">
        <label>
          Load weight (lb)
          <input
            name="load_weight_lb"
            type="number"
            min="0"
            max="200000"
            step="100"
            value={draft.load_weight_lb}
            placeholder="e.g. 35000"
            onChange={update}
            required
          />
        </label>
        <label>
          Sample every
          <select name="sample_miles" value={draft.sample_miles} onChange={update}>
            <option value="10">10 miles</option>
            <option value="25">25 miles</option>
            <option value="50">50 miles</option>
          </select>
        </label>
      </div>
      <button type="submit" disabled={loading}>
        {loading ? "Planning routes…" : "Compare routes"}
      </button>
    </form>
  );
}
