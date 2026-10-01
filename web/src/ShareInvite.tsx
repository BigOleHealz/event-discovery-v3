import { useEffect, useRef, useState } from "react";
import { createdLink, linkRequest, type CreatedLink } from "./inviteLinks";

export function ShareInvite({ apiBaseUrl, eventId }: { apiBaseUrl: string; eventId: string }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [link, setLink] = useState<CreatedLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function create() {
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setStatus("");
    try {
      const value = createdLink(await linkRequest(apiBaseUrl, "", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ canonical_event_id: eventId, message: message || null }),
      }));
      if (!controller.signal.aborted) { setLink(value); setStatus("Link created. Choose how to share it."); }
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setStatus(reason instanceof Error ? reason.message : "Unable to create link.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  async function share() {
    if (!link) return;
    try {
      await navigator.share({ title: "Join me at this event", url: link.url });
      setStatus("Share sheet closed. Check Share links for acceptances.");
    } catch (reason: unknown) {
      setStatus((reason instanceof DOMException || reason instanceof Error) && reason.name === "AbortError" ?
        "Sharing cancelled. Your link is still available." : "Sharing unavailable. Copy the link instead.");
    }
  }
  async function copy() {
    if (!link) return;
    try { await navigator.clipboard.writeText(link.url); setStatus("Link copied. Paste it into your message."); }
    catch { setStatus("Select the link below and copy it manually."); }
  }
  return <section className="detail-section invite-form" aria-label="Share invitation">
    <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>Share invite</button>
    {open && <>
      <p>Invite anyone through your own messaging app. They sign in with Google and choose Accept.</p>
      {!link ? <form onSubmit={(event) => { event.preventDefault(); void create(); }}>
        <label>Message for shared invite (optional)
          <textarea maxLength={1000} value={message} disabled={busy} onChange={(event) => setMessage(event.target.value)} />
        </label>
        <button disabled={busy} type="submit">{busy ? "Creating…" : "Create link"}</button>
      </form> : <>
        {typeof navigator.share === "function" && <button type="button" onClick={() => { void share(); }}>Share link</button>}
        <button type="button" onClick={() => { void copy(); }}>Copy link</button>
        <label>Invitation link<input readOnly value={link.url} onFocus={(event) => event.target.select()} /></label>
        <p>Expires {new Date(link.expires_at).toLocaleString()}. Save this link now; only its tracking is available after closing.</p>
      </>}
      <p>Creating, sharing, or copying a link does not confirm that a message was sent or delivered.</p>
      {status && <p role="status">{status}</p>}
    </>}
  </section>;
}
