import { useEffect, useRef, useState } from "react";
import { sendInvites } from "./invites";

export function InviteForm({ apiBaseUrl, eventId }: { apiBaseUrl: string; eventId: string }) {
  const [emails, setEmails] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function send() {
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setStatus("");
    try {
      const invites = await sendInvites(apiBaseUrl, eventId,
        emails.split(",").map((email) => email.trim()).filter(Boolean), message, controller.signal);
      if (!controller.signal.aborted) {
        setStatus(`${invites.length} ${invites.length === 1 ? "invitation saved" : "invitations saved"}. Check Sent for responses.`);
        setEmails("");
        setMessage("");
      }
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setStatus(reason instanceof Error ? reason.message : "Unable to send invites.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="detail-section invite-form" aria-label="Invite friends">
    <h3>Invite friends</h3>
    <form onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <label>Friends’ account emails
        <input type="email" multiple required value={emails} disabled={busy}
          onChange={(event) => setEmails(event.target.value)} />
      </label>
      <p className="detail-muted">Separate emails with commas. Friends need an existing account.</p>
      <label>Message (optional)
        <textarea maxLength={1000} value={message} disabled={busy}
          onChange={(event) => setMessage(event.target.value)} />
      </label>
      <button type="submit" disabled={busy}>{busy ? "Sending…" : "Send invites"}</button>
    </form>
    {status && <p role="status">{status}</p>}
  </section>;
}
