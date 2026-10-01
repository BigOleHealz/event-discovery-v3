import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { friendsChanged, friendsRequest, friendships, type Friendship } from "./friends";
import { FriendAvatar } from "./FriendAvatar";

export function FriendsPanel({ apiBaseUrl, onClose }: { apiBaseUrl: string; onClose: () => void }) {
  const [rows, setRows] = useState<Friendship[]>([]);
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const request = useRef<AbortController | null>(null);
  const panel = useRef<HTMLElement>(null);
  const base = `${apiBaseUrl.replace(/\/$/, "")}/api/friends`;
  useEffect(() => { panel.current?.focus(); return () => request.current?.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    void friendsRequest(base, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setRows(friendships(value));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) { setRows([]); setStatus(error instanceof Error ? error.message : "Friends unavailable."); }
    });
    return () => controller.abort();
  }, [base, version]);

  async function change(path: string, body?: object) {
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setStatus("");
    try {
      await friendsRequest(base + path, { method: "POST", signal: controller.signal,
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
      if (!controller.signal.aborted) {
        setEmail(""); setVersion((v) => v + 1); friendsChanged();
        setStatus(path === "" ? "Request saved. They must accept before you can see each other's attendance." : "Friends updated.");
      }
    } catch (error: unknown) {
      if (!controller.signal.aborted) setStatus(error instanceof Error ? error.message : "Unable to update friends.");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return createPortal(<aside className="invite-inbox friends-panel" role="dialog" aria-modal="false"
    aria-label="Friends" tabIndex={-1} ref={panel} onKeyDown={(e) => { if (e.key === "Escape" && !busy) onClose(); }}>
    <button type="button" disabled={busy} onClick={onClose} aria-label="Close friends">Close</button>
    <h2>Friends</h2>
    <p>Accepted friends can see which events each other is going to. Event invitations do not add friends automatically.</p>
    <form onSubmit={(e) => { e.preventDefault(); void change("", { email }); }}>
      <label>Friend’s account email<input type="email" required value={email} disabled={busy}
        onChange={(e) => setEmail(e.target.value)} /></label>
      <button disabled={busy} type="submit">Send friend request</button>
    </form>
    <button type="button" disabled={busy} onClick={() => { setStatus(""); setVersion((v) => v + 1); friendsChanged(); }}>Refresh friends</button>
    {(["incoming", "outgoing", "accepted"] as const).map((kind) => <section key={kind}>
      <h3>{{ incoming: "Incoming requests", outgoing: "Sent requests", accepted: "Your friends" }[kind]}</h3>
      {rows.filter((row) => row.status === kind).map((row) => <div className="friend-row" key={row.id}>
        <FriendAvatar person={row} />
        <span>{row.display_name}</span>
        {kind === "incoming" ? <button disabled={busy} type="button" onClick={() => void change(`/${row.id}/accept`)}>Accept friend request</button> : null}
        <button disabled={busy} type="button" onClick={() => void change(`/${row.id}/remove`)}>
          {{ incoming: "Decline", outgoing: "Cancel request", accepted: "Remove friend" }[kind]}
        </button>
      </div>)}
      {!rows.some((row) => row.status === kind) ? <p>None yet.</p> : null}
    </section>)}
    <p role="status">{status}</p>
  </aside>, document.body);
}
