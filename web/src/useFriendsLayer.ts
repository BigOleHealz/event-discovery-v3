import { useEffect, useState } from "react";
import { eventsUrl, type EventFilters, type EventViewport } from "./events";
import { friendsLayer, friendsRequest } from "./friends";

const EMPTY = new Map<string, number>();

export function useFriendsLayer(base: string, userId: string | null | undefined, enabled: boolean,
  viewport: EventViewport | undefined, filters: EventFilters, stale: boolean) {
  const [version, setVersion] = useState(0);
  const url = eventsUrl(base, viewport, filters);
  url.pathname += "/friends";
  const href = url.href;
  const key = enabled && userId && viewport && !stale ? `${userId}:${href}` : "";
  const [result, setResult] = useState({ key: "", badges: EMPTY, error: "" });
  useEffect(() => {
    if (!key) return;
    let request: AbortController | null = null;
    function load() {
      request?.abort(); const controller = new AbortController(); request = controller;
      if (!navigator.onLine) { setResult({ key, badges: EMPTY, error: "Connect to see friends going." }); return; }
      void friendsRequest(href, { signal: controller.signal }).then((value) => {
        if (controller.signal.aborted) return;
        const layer = friendsLayer(value);
        const badges = new Map<string, number>();
        for (const event of layer.events) if (event.friends.length) badges.set(event.event_id, event.friends.length);
        for (const cell of layer.cells) badges.set(cell.cell_id, cell.event_count);
        setResult({ key, badges, error: "" });
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) setResult({ key, badges: EMPTY,
          error: error instanceof Error ? error.message : "Friends unavailable." });
      });
    }
    const refresh = () => { if (document.visibilityState === "visible") load(); };
    load();
    const timer = window.setInterval(refresh, 30_000);
    for (const name of ["friends-changed", "focus", "online", "offline"]) window.addEventListener(name, refresh);
    return () => {
      request?.abort(); window.clearInterval(timer);
      for (const name of ["friends-changed", "focus", "online", "offline"]) window.removeEventListener(name, refresh);
    };
  }, [key, href, version]);
  return { badges: key && result.key === key ? result.badges : EMPTY,
    error: key && result.key === key ? result.error : "",
    refresh: () => setVersion((v) => v + 1) };
}
