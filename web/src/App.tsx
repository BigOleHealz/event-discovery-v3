import { EventMap } from "./EventMap";
import { DedupReview } from "./DedupReview";
import { loadApiBaseUrl, loadConfig } from "./config";
import "./styles.css";

export function App() {
  let config;
  const reviewing = window.location.pathname.replace(/\/$/, "") === "/admin/dedup";
  let apiBaseUrl;
  try {
    apiBaseUrl = loadApiBaseUrl();
    if (!reviewing) config = loadConfig();
  } catch (reason: unknown) {
    const message = reason instanceof Error ? reason.message : "Application configuration is invalid";
    return (
      <main className="configuration-error">
        <p className="eyebrow">Configuration needed</p>
        <h1>Event Discovery</h1>
        <p>{message}</p>
      </main>
    );
  }

  if (reviewing) return <DedupReview apiBaseUrl={apiBaseUrl} />;
  if (!config) return null;

  return (
    <main className="app-shell">
      <header className="brand-card">
        <p className="eyebrow">Philadelphia</p>
        <h1>Find something worth going to.</h1>
      </header>
      <EventMap
        apiBaseUrl={config.apiBaseUrl}
        apiKey={config.googleMapsApiKey}
        mapId={config.googleMapsMapId}
      />
    </main>
  );
}
