import { InviteLinkPreview } from "./InviteLinkPreview";
import { pendingInvitation } from "./inviteLinks";
import { EventMap } from "./EventMap";
import { DedupReview } from "./DedupReview";
import { AccountControls } from "./AccountControls";
import { loadApiBaseUrl, loadConfig } from "./config";
import "./styles.css";

export function App() {
  const [inviteToken, setInviteToken] = useState(pendingInvitation);
  const [inviteVersion, setInviteVersion] = useState(0);
  useEffect(() => {
    const openInvitation = () => {
      setInviteToken(pendingInvitation());
      setInviteVersion((value) => value + 1);
    };
    window.addEventListener("hashchange", openInvitation);
    return () => window.removeEventListener("hashchange", openInvitation);
  }, []);
  const [userId, setUserId] = useState<string | null>(null);
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
      <AccountControls apiBaseUrl={config.apiBaseUrl} onUserChange={setUserId} />
      {inviteToken && <InviteLinkPreview key={`${inviteToken}:${userId ?? ""}:${inviteVersion}`} apiBaseUrl={config.apiBaseUrl}
        token={inviteToken} userId={userId} onClose={() => setInviteToken(null)} />}
      <EventMap
        userId={userId}
        apiBaseUrl={config.apiBaseUrl}
        apiKey={config.googleMapsApiKey}
        mapId={config.googleMapsMapId}
      />
    </main>
  );
}
import { useEffect, useState } from "react";
