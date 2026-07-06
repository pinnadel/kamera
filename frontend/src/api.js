// In production the frontend is served by the same FastAPI process it talks
// to, so a relative base lets the app work regardless of which hostname was
// used to reach it (localhost, a LAN IP, or a Tailscale address/hostname).
// In dev, the Vite server (:5173) and the backend (:8000) are different
// origins — reuse whatever hostname the browser used to load the page so
// remote dev access works the same way.
export const API = import.meta.env.DEV
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : ''
