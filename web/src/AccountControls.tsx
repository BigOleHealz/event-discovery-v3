import { useEffect, useState } from "react";
import { InviteInbox } from "./InviteInbox";

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
  const [showInvites, setShowInvites] = useState(false);
  useEffect(() => { onUserChange?.(user?.id ?? null); }, [user, onUserChange]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(() =>
    new URLSearchParams(window.location.search).has("auth_error")
      ? "Sign-in did not finish. Please try again." : "",
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
          <button type="button" onClick={() => setShowInvites(true)}>Invitations</button>
          <button type="button" disabled={busy} onClick={() => { void signOut(); }}>
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </>
      ) : <a href={`${base}/api/auth/google/start`}>Sign in with Google</a>}
      {message && <p role="status">{message}</p>}
      {user && showInvites && <InviteInbox key={user.id} apiBaseUrl={apiBaseUrl}
        onClose={() => setShowInvites(false)} />}
    </section>
  );
}
