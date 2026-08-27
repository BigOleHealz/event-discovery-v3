import { EventMap } from "./EventMap";
import { loadConfig } from "./config";
import "./styles.css";

export function App() {
  let config;
  try {
    config = loadConfig();
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
