import { useEffect, useRef, useState } from "react";
import { ContactList } from "./ContactList";
import { sendInvites } from "./invites";

export function InviteForm({ apiBaseUrl, eventId }: { apiBaseUrl: string; eventId: string }) {
  const [emails, setEmails] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [showContacts, setShowContacts] = useState(false);
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
      const addresses = emails.split(",").map((value) => value.trim()).filter(Boolean);
      const invites = await sendInvites(apiBaseUrl, eventId,
        addresses.filter((value) => value.includes("@")), message, controller.signal,
        selected, addresses.filter((value) => !value.includes("@")));
      if (!controller.signal.aborted) {
        setStatus(`${invites.length} ${invites.length === 1 ? "invitation saved" : "invitations saved"}. Check Sent for responses.`);
        setEmails("");
        setMessage("");
        setSelected([]);
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
      <button type="button" aria-expanded={showContacts} disabled={busy}
        onClick={() => setShowContacts((value) => !value)}>Choose contacts ({selected.length} selected)</button>
      {showContacts && <ContactList apiBaseUrl={apiBaseUrl} selected={selected} disabled={busy}
        onToggle={(id) => setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} />}
      <label>Friends’ emails or phone numbers
        <input type="text" required={selected.length === 0} value={emails} disabled={busy}
          onChange={(event) => setEmails(event.target.value)} />
      </label>
      <p className="detail-muted">Separate addresses with commas. Use registered emails or international +country-code numbers. Unmatched numbers receive an SMS.</p>
      <label>Message (optional)
        <textarea maxLength={1000} value={message} disabled={busy}
          onChange={(event) => setMessage(event.target.value)} />
      </label>
      <button type="submit" disabled={busy}>{busy ? "Sending…" : "Send invites"}</button>
    </form>
    {status && <p role="status">{status}</p>}
  </section>;
}
