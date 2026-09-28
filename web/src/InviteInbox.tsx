import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";
import { listInvites, respondToInvite, type Invite } from "./invites";

export function InviteInbox({ apiBaseUrl, onClose }: { apiBaseUrl: string; onClose: () => void }) {
  const [kind, setKind] = useState<"received" | "sent">("received");
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [items, setItems] = useState<Invite[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panel = useRef<HTMLElement>(null);
  const mutation = useRef<AbortController | null>(null);

  useEffect(() => {
    panel.current?.focus();
    return () => mutation.current?.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      try {
        const invites = await listInvites(apiBaseUrl, kind, offset, controller.signal);
        if (!controller.signal.aborted) setItems(invites);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setItems([]);
          setError(reason instanceof Error ? reason.message : "Unable to load invites.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [apiBaseUrl, kind, offset, version]);

  async function respond(id: string, response: "accept" | "decline") {
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setError("");
    try {
      const updated = await respondToInvite(apiBaseUrl, id, response, controller.signal);
      if (!controller.signal.aborted) setItems((current) => current.map((item) => item.id === id ? updated : item));
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to respond.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return createPortal(<aside className="invite-inbox" role="dialog" aria-modal="false" aria-label="Invitations"
    ref={panel} tabIndex={-1} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <button type="button" onClick={onClose} aria-label="Close invitations">Close</button>
    <h2>Invitations</h2>
    <nav aria-label="Invitation lists">
      {(["received", "sent"] as const).map((tab) => <button type="button" key={tab}
        aria-pressed={kind === tab} disabled={busy} onClick={() => { setKind(tab); setOffset(0); }}>
        {tab === "received" ? "Received" : "Sent"}
      </button>)}
      <button type="button" disabled={busy || loading} onClick={() => setVersion((value) => value + 1)}>Refresh</button>
    </nav>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading invitations…</p> : items.length === 0 ? <p>No invitations here yet.</p> :
      <ul>{items.map((invite) => <li key={invite.id}>
        <h3>{invite.event_title}</h3>
        <time dateTime={invite.starts_at}>{new Date(invite.starts_at).toLocaleString()}</time>
        <p>{kind === "received" ? `From ${invite.inviter_names.join(", ")}` : `To ${invite.recipient_name ?? "your friend"}`}</p>
        {invite.message && <p>{invite.message}</p>}
        <p role="status">{invite.status === "accepted" ? (kind === "received" ? "Accepted — you’re going" : "Accepted") : invite.status === "declined" ? "Declined" : "Pending"}</p>
        {kind === "received" && <div className="invite-response">
          <button type="button" disabled={busy || invite.status === "accepted"}
            onClick={() => { void respond(invite.id, "accept"); }}>Accept</button>
          <button type="button" disabled={busy || invite.status === "declined"}
            onClick={() => { void respond(invite.id, "decline"); }}>Decline</button>
        </div>}
      </li>)}</ul>}
    <nav aria-label="Invitation pages">
      <button type="button" disabled={loading || busy || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - 20))}>Previous</button>
      <button type="button" disabled={loading || busy || items.length < 20} onClick={() => setOffset((value) => value + 20)}>Next</button>
    </nav>
  </aside>, document.body);
}
