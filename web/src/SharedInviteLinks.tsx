import { useEffect, useRef, useState } from "react";
import { linkRequest, linkTracking, type LinkTracking } from "./inviteLinks";

export function SharedInviteLinks({ apiBaseUrl }: { apiBaseUrl: string }) {
  const [links, setLinks] = useState<LinkTracking[]>([]);
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    void linkRequest(apiBaseUrl, `?limit=20&offset=${offset}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setLinks(linkTracking(value)); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) { setLinks([]); setError(reason instanceof Error ? reason.message : "Unable to load links."); }
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [apiBaseUrl, offset, version]);
  function refresh() { setLoading(true); setError(""); setVersion((value) => value + 1); }
  async function revoke(id: string) {
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      await linkRequest(apiBaseUrl, `/${encodeURIComponent(id)}/revoke`, { method: "POST", signal: controller.signal });
      if (!controller.signal.aborted) refresh();
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to revoke link.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section aria-label="Share link tracking">
    <p>Opens may include previews or bots. Sharing and copying do not confirm delivery.</p>
    <button type="button" disabled={busy || loading} onClick={refresh}>Refresh links</button>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading share links…</p> : <>
      {links.length === 0 && <p>No share links yet.</p>}
      <ul>{links.map((link) => <li key={link.id}>
        <h3>{link.event_title}</h3>
        {link.message && <p>{link.message}</p>}
        <p>Link created: {new Date(link.created_at).toLocaleString()}</p>
        <p>{link.first_opened_at ? `Opened: ${new Date(link.first_opened_at).toLocaleString()}` : "No opens observed"}</p>
        <p>Accepted: {link.acceptance_count}</p>
        {link.accepted_by.length > 0 && <ul>{link.accepted_by.map((person) =>
          <li key={person.user_id}>{person.display_name} — accepted {new Date(person.accepted_at).toLocaleString()}</li>)}</ul>}
        {link.acceptance_count > link.accepted_by.length && <p>Showing the first {link.accepted_by.length} acceptances.</p>}
        <p>{link.revoked_at ? "Revoked" : `Expires: ${new Date(link.expires_at).toLocaleString()}`}</p>
        {!link.revoked_at && <button type="button" disabled={busy} onClick={() => { void revoke(link.id); }}>Revoke link</button>}
      </li>)}</ul>
    </>}
    <nav aria-label="Share link pages">
      <button type="button" disabled={busy || loading || offset === 0} onClick={() => { setLoading(true); setError(""); setOffset((value) => Math.max(0, value - 20)); }}>Previous links</button>
      <button type="button" disabled={busy || loading || links.length < 20} onClick={() => { setLoading(true); setError(""); setOffset((value) => value + 20); }}>Next links</button>
    </nav>
  </section>;
}
