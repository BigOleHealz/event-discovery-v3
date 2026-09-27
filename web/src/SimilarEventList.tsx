import { useCallback, useEffect, useState } from "react";

import type { EventFeature } from "./events";
import { fetchSimilarEvents, type SimilarEvent } from "./similarEvents";

interface Props {
  apiBaseUrl: string;
  eventId: string;
  onSelect: (event: EventFeature) => void;
}

type State = { kind: "loading" } | { kind: "error" } |
  { kind: "ready"; items: SimilarEvent[] };

// The parent keys this component by event/API so old recommendations disappear
// immediately when the selected event changes, even if its request arrives late.
export function SimilarEvents({ apiBaseUrl, eventId, onSelect }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetchSimilarEvents(apiBaseUrl, eventId, controller.signal).then(
      (items) => { if (!controller.signal.aborted) setState({ kind: "ready", items }); },
      () => { if (!controller.signal.aborted) setState({ kind: "error" }); },
    );
    return () => controller.abort();
  }, [apiBaseUrl, eventId, attempt]);

  useEffect(() => {
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, [retry]);

  return <section className="detail-section" aria-labelledby="similar-events-title">
    <h3 id="similar-events-title">Similar events</h3>
    <div aria-live="polite">
      {state.kind === "loading" ? <p className="detail-muted">Finding similar events…</p> : null}
      {state.kind === "error" ? <>
        <p className="detail-muted">Similar events are unavailable right now.</p>
        <button type="button" className="similar-retry" onClick={retry}>Retry similar events</button>
      </> : null}
      {state.kind === "ready" && state.items.length === 0 ?
        <p className="detail-muted">No similar upcoming events found.</p> : null}
    </div>
    {state.kind === "ready" && state.items.length > 0 ? <ul className="similar-events">
      {state.items.map(({ event }) => <li key={event.id}>
        <button type="button" onClick={() => onSelect(event)}>
          <span>{event.properties.title}</span>
          <time dateTime={event.properties.starts_at}>
            {new Intl.DateTimeFormat(undefined, {
              dateStyle: "medium", timeStyle: "short", timeZone: event.properties.timezone,
            }).format(new Date(event.properties.starts_at))}
          </time>
        </button>
      </li>)}
    </ul> : null}
  </section>;
}
