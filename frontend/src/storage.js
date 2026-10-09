// storage.js - remembering small things in this browser (theme, "show why" links).
// Browser storage can be missing or blocked (private windows, strict settings); then the app simply
// forgets these preferences instead of crashing.
export function load(key, fallback = null) {
  try {
    const v = window.localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch {
    return fallback;
  }
}

export function save(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: fine, it just won't be remembered */
  }
}
