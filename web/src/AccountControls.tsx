import { ContactsPanel } from "./ContactsPanel";
import { useEffect, useState } from "react";
import { InviteInbox } from "./InviteInbox";
import { EventFeedback } from "./EventFeedback";
import googleSignInDark from "./assets/google-sign-in-dark.png";

interface User {
  id: string;
  email: string | null;
  display_name: string | null;
}

async function readUser(response: Response): Promise<User> {
  const value: unknown = await response.json();
  if (typeof value !== "object" || value === null || !("id" in value) ||
      typeof value.id !== "string" || !("email" in value) ||
      !(value.email === null || typeof value.email === "string") || !("display_name" in value) ||
      !(value.display_name === null || typeof value.display_name === "string")) {
    throw new Error("Invalid account response");
  }
  return { id: value.id, email: value.email, display_name: value.display_name };
}

export function AccountControls({ apiBaseUrl, onUserChange }: {
  apiBaseUrl: string; onUserChange?: (userId: string | null) => void;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [showContacts, setShowContacts] = useState(() => new URLSearchParams(window.location.search).has("contacts_imported"));
  const [showInvites, setShowInvites] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  useEffect(() => { onUserChange?.(user?.id ?? null); }, [user, onUserChange]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(() =>
    new URLSearchParams(window.location.search).has("auth_error")
      ? "Sign-in did not finish. Please try again." : new URLSearchParams(window.location.search).has("contacts_error")
        ? "Google Contacts import did not finish. Please try again or upload a vCard." : "",
  );
  const base = apiBaseUrl.replace(/\/$/, "");

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`${base}/api/me`, {
          credentials: "include", cache: "no-store", signal: controller.signal,
        });
        if (response.status === 401) setUser(null);
        else if (response.status === 503) setMessage("Sign-in is not available yet.");
        else if (!response.ok) throw new Error("Account unavailable");
        else setUser(await readUser(response));
      } catch {
        if (!controller.signal.aborted) setMessage("Unable to load your account. Try again online.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [base]);

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch(`${base}/api/auth/refresh`, {
          method: "POST", credentials: "include", cache: "no-store", signal: controller.signal,
        });
        if (response.status === 401) {
          setUser(null);
          setMessage("Your session expired. Sign in again.");
        } else if (!response.ok) {
          setMessage("Unable to refresh your session. Try again online.");
        }
      } catch {
        if (!controller.signal.aborted) setMessage("Unable to refresh your session. Try again online.");
      }
    }
    // A thirty-second heartbeat also supports the minimum configurable cookie lifetime.
    const timer = window.setInterval(() => { void refresh(); }, 30_000);
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [base, user]);

  async function signOut() {
    setBusy(true);
    try {
      const response = await fetch(`${base}/api/auth/logout`, {
        method: "POST", credentials: "include", cache: "no-store",
      });
      if (!response.ok) throw new Error("Sign-out failed");
      setUser(null);
      setShowInvites(false);
      setShowFeedback(false);
      setShowContacts(false);
      setMessage("");
    } catch {
      setMessage("Unable to sign out. Please try again online.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="account-controls" aria-label="Your account">
      {loading ? <span role="status">Loading account…</span> : user ? (
        <>
          <span>Signed in as {user.display_name ?? user.email ?? "you"}</span>
          <button type="button" onClick={() => { setShowContacts(false); setShowFeedback(false); setShowInvites(true); }}>Invitations</button>
          <button type="button" onClick={() => { setShowContacts(false); setShowInvites(false); setShowFeedback(true); }}>Event feedback</button>
          <button type="button" onClick={() => { setShowInvites(false); setShowFeedback(false); setShowContacts(true); }}>Contacts</button>
          <button type="button" disabled={busy} onClick={() => { void signOut(); }}>
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </>
      ) : <a className="google-sign-in" href={`${base}/api/auth/google/start`}>
        <img src={googleSignInDark} width="180" height="40" alt="Sign in with Google" />
      </a>}
      {message && <p role="status">{message}</p>}
      {user && showContacts && <ContactsPanel key={user.id} apiBaseUrl={apiBaseUrl}
        onClose={() => setShowContacts(false)} />}
      {user && showInvites && <InviteInbox key={user.id} apiBaseUrl={apiBaseUrl}
        onClose={() => setShowInvites(false)} />}
      {user && showFeedback && <EventFeedback key={user.id} apiBaseUrl={apiBaseUrl}
        onClose={() => setShowFeedback(false)} />}
    </section>
  );
}
