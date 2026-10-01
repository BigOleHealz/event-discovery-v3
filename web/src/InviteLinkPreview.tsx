import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { forgetInvitation, linkPreview, linkRequest, type LinkPreview } from "./inviteLinks";

export function InviteLinkPreview({ apiBaseUrl, token, userId, onClose }: {
  apiBaseUrl: string; token: string; userId: string | null; onClose: () => void;
}) {
  const [preview, setPreview] = useState<LinkPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const request = useRef<AbortController | null>(null);
  const panel = useRef<HTMLElement>(null);
  const base = apiBaseUrl.replace(/\/$/, "");
  useEffect(() => {
    panel.current?.focus();
    const controller = new AbortController();
    void linkRequest(base, `/${encodeURIComponent(token)}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setPreview(linkPreview(value)); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to open invitation."); });
    return () => { controller.abort(); request.current?.abort(); };
  }, [base, token]);

  async function accept() {
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      await linkRequest(base, `/${encodeURIComponent(token)}/accept`, { method: "POST", signal: controller.signal });
      if (!controller.signal.aborted) { setAccepted(true); forgetInvitation(); }
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to accept invitation.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  function close() { forgetInvitation(); onClose(); }
  return createPortal(<aside className="invite-inbox" role="dialog" aria-label="Event invitation"
    aria-modal="false" ref={panel} tabIndex={-1} onKeyDown={(event) => { if (event.key === "Escape") close(); }}>
    <button type="button" onClick={close}>Close invitation</button>
    <h2>Event invitation</h2>
    {error && <p role="alert">{error}</p>}
    {!preview && !error && <p role="status">Loading invitation…</p>}
    {preview && <>
      <h3>{preview.event_title}</h3>
      <p>From {preview.inviter_name}</p>
      <time dateTime={preview.starts_at}>{new Date(preview.starts_at).toLocaleString()}</time>
      {preview.message && <p>{preview.message}</p>}
      <p>Expires {new Date(preview.expires_at).toLocaleString()}.</p>
      <a href={`/?event=${encodeURIComponent(preview.canonical_event_id)}`} target="_blank" rel="noreferrer">View event details</a>
      {accepted ? <p role="status">Accepted — you’re going</p> : userId ?
        <button disabled={busy} type="button" onClick={() => { void accept(); }}>{busy ? "Accepting…" : "Accept invitation"}</button> :
        <p><a href={`${base}/api/auth/google/start`}>Sign in with Google to accept</a></p>}
      {!accepted && <p>Opening this link or signing in does not accept it. Choose Accept when you are ready.</p>}
    </>}
  </aside>, document.body);
}
