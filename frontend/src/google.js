// Loads the Google Maps JS API (Places only) once, on demand.
// Without VITE_GOOGLE_MAPS_API_KEY this resolves to null and the place
// inputs fall back to plain text that the server geocodes.

const KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY || "").trim();
const REGIONS = (import.meta.env.VITE_PLACES_REGIONS || "")
  .split(",")
  .map((code) => code.trim().toLowerCase())
  .filter(Boolean);

let placesPromise = null;
let authFailed = false;

export const placesEnabled = Boolean(KEY);
export const placesRegions = REGIONS;

function loadScript() {
  return new Promise((resolve, reject) => {
    if (window.google?.maps?.importLibrary) {
      resolve(window.google.maps);
      return;
    }
    const callback = "__weatherRoutingGoogleReady";
    window[callback] = () => {
      delete window[callback];
      resolve(window.google.maps);
    };
    // Google calls this global when the key is invalid or not allowed for this site.
    window.gm_authFailure = () => {
      authFailed = true;
      console.warn("Google Maps rejected the API key. Falling back to server geocoding.");
    };
    const params = new URLSearchParams({ key: KEY, v: "weekly", loading: "async", callback });
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
    script.async = true;
    script.onerror = () => reject(new Error("Google Maps could not be loaded."));
    document.head.append(script);
  });
}

export function loadPlaces() {
  if (!KEY || authFailed) return Promise.resolve(null);
  if (!placesPromise) {
    placesPromise = loadScript()
      .then((maps) => maps.importLibrary("places"))
      .catch((error) => {
        placesPromise = null;
        console.warn(error);
        return null;
      });
  }
  return placesPromise.then((places) => (authFailed ? null : places));
}
