import { useEffect, useId, useRef, useState } from "react";
import { loadPlaces, placesEnabled, placesRegions } from "../google";

const DEBOUNCE_MS = 200;
const MIN_CHARS = 2;

// Text input with Google Places suggestions (Places API "New",
// AutocompleteSuggestion). Picking a suggestion reports {label, lat, lon}
// through onPlace; typing again clears it so the server geocodes the text.
export default function PlaceInput({ name, label, value, place, onText, onPlace, placeholder }) {
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [error, setError] = useState("");
  const sessionRef = useRef(null);
  const requestRef = useRef(0);
  const timerRef = useRef(null);
  const listId = useId();

  useEffect(() => () => clearTimeout(timerRef.current), []);

  function search(text) {
    clearTimeout(timerRef.current);
    const request = ++requestRef.current;
    if (!placesEnabled || text.trim().length < MIN_CHARS) {
      setSuggestions([]);
      return;
    }
    timerRef.current = setTimeout(async () => {
      const places = await loadPlaces();
      if (!places || request !== requestRef.current) return;
      if (!sessionRef.current) sessionRef.current = new places.AutocompleteSessionToken();
      const options = { input: text, sessionToken: sessionRef.current };
      if (placesRegions.length) options.includedRegionCodes = placesRegions;
      try {
        const result = await places.AutocompleteSuggestion.fetchAutocompleteSuggestions(options);
        if (request !== requestRef.current) return;
        setSuggestions(
          (result.suggestions || [])
            .map((item) => item.placePrediction)
            .filter(Boolean)
            .slice(0, 5),
        );
        setActive(-1);
        setOpen(true);
        setError("");
      } catch (err) {
        console.warn(err);
        if (request === requestRef.current) setSuggestions([]);
      }
    }, DEBOUNCE_MS);
  }

  async function choose(prediction) {
    const text = prediction.text.toString();
    requestRef.current += 1;
    setOpen(false);
    setSuggestions([]);
    onText(text);
    try {
      const placeObject = prediction.toPlace();
      await placeObject.fetchFields({ fields: ["location", "formattedAddress", "displayName"] });
      sessionRef.current = null; // a pick ends the billing session
      const location = placeObject.location;
      if (!location) throw new Error("Place has no location.");
      onPlace({
        label: placeObject.formattedAddress || text,
        lat: location.lat(),
        lon: location.lng(),
      });
      setError("");
    } catch (err) {
      console.warn(err);
      onPlace(null);
      setError("Could not pin that place. The typed name will be looked up instead.");
    }
  }

  function onChange(event) {
    const text = event.target.value;
    onText(text);
    if (place) onPlace(null);
    search(text);
  }

  function onKeyDown(event) {
    if (!open || !suggestions.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => (index + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => (index <= 0 ? suggestions.length - 1 : index - 1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      choose(suggestions[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  const showList = open && suggestions.length > 0;

  return (
    <label className="place-field">
      <span className="place-label">
        {label}
        {place && <span className="pinned" title={place.label}>Pinned</span>}
      </span>
      <div className="place-wrap">
        <input
          name={name}
          value={value}
          onChange={onChange}
          onKeyDown={onKeyDown}
          onFocus={() => suggestions.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          placeholder={placeholder}
          autoComplete="off"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          required
        />
        {showList && (
          <ul className="suggestions" id={listId} role="listbox">
            {suggestions.map((prediction, index) => (
              <li
                key={prediction.placeId}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={index === active ? "active" : ""}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(prediction);
                }}
                onMouseEnter={() => setActive(index)}
              >
                <strong>{prediction.mainText?.toString() || prediction.text.toString()}</strong>
                {prediction.secondaryText && <span>{prediction.secondaryText.toString()}</span>}
              </li>
            ))}
            <li className="powered" aria-hidden="true">powered by Google</li>
          </ul>
        )}
      </div>
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}
