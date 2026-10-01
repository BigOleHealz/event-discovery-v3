import { useEffect, useState } from "react";
import { friendsRequest, people, type FriendPerson } from "./friends";
import { FriendAvatar } from "./FriendAvatar";

export function FriendsGoing({ apiBaseUrl, eventId }: { apiBaseUrl: string; eventId: string }) {
  const [rows, setRows] = useState<FriendPerson[]>([]);
  const [status, setStatus] = useState("Loading friends…");
  useEffect(() => {
    let request: AbortController | null = null;
    function load() {
      request?.abort();
      const controller = new AbortController(); request = controller;
      if (!navigator.onLine) { setRows([]); setStatus("Connect to see friends going."); return; }
      void friendsRequest(`${apiBaseUrl.replace(/\/$/, "")}/api/events/${eventId}/friends`, { signal: controller.signal })
        .then((value) => { if (!controller.signal.aborted) { setRows(people(value)); setStatus(""); } })
        .catch((error: unknown) => { if (!controller.signal.aborted) {
          setRows([]); setStatus(error instanceof Error ? error.message : "Friends unavailable.");
        } });
    }
    const refresh = () => { if (document.visibilityState === "visible") load(); };
    load();
    const timer = window.setInterval(refresh, 30_000);
    for (const name of ["online", "offline", "friends-changed", "focus"]) window.addEventListener(name, refresh);
    return () => {
      request?.abort(); window.clearInterval(timer);
      for (const name of ["online", "offline", "friends-changed", "focus"]) window.removeEventListener(name, refresh);
    };
  }, [apiBaseUrl, eventId]);
  return <section className="detail-section" aria-label="Friends going">
    <h3>Friends going</h3>
    {rows.map((person) => <div className="friend-row" key={person.id}>
      <FriendAvatar person={person} />
      <span>{person.display_name}</span>
    </div>)}
    <p role="status">{status || (!rows.length ? "No friends going yet." : "")}</p>
  </section>;
}
