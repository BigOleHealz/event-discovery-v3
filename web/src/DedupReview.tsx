import { useEffect, useRef, useState } from "react";

interface ListingSnapshot {
  listing_id: string;
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string | null;
  timezone: string;
  venue: string | null;
  address: string | null;
  source: string;
  url: string;
}

interface ReviewPair {
  id: string;
  snapshot_a: ListingSnapshot;
  snapshot_b: ListingSnapshot;
  similarity_score: number;
  time_delta_minutes: number;
  distance_meters: number;
  event_a_id: string;
  event_b_id: string;
}

interface ReviewQueue {
  pending: number;
  pair: ReviewPair | null;
}

type Decision = "merged" | "distinct" | "skipped";

function ListingCard({ listing }: { listing: ListingSnapshot }) {
  const formatTime = (value: string) => new Intl.DateTimeFormat(undefined, {
    dateStyle: "full", timeStyle: "short", timeZone: listing.timezone,
  }).format(new Date(value));
  const safeUrl = /^https?:\/\//i.test(listing.url);
  return (
    <article className="review-listing">
      <p className="eyebrow">{listing.source}</p>
      <h2>{listing.title}</h2>
      <p><time dateTime={listing.starts_at}>{formatTime(listing.starts_at)}</time></p>
      {listing.ends_at ? <p>Ends {formatTime(listing.ends_at)}</p> : null}
      <p className="review-muted">{listing.timezone}</p>
      <h3>{listing.venue ?? "Venue unavailable"}</h3>
      <p>{listing.address ?? "Address unavailable"}</p>
      <p className="review-description">{listing.description ?? "No description supplied."}</p>
      {safeUrl ? (
        <a href={listing.url} target="_blank" rel="noopener noreferrer">
          Open {listing.source} listing
        </a>
      ) : <p>Source link unavailable</p>}
    </article>
  );
}

export function DedupReview({ apiBaseUrl }: { apiBaseUrl: string }) {
  const [token, setToken] = useState("");
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const submitting = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const endpoint = new URL(
    `${apiBaseUrl.replace(/\/$/, "")}/api/admin/dedup`, `${window.location.origin}/`,
  ).href;

  async function request<T>(credential: string, path = "", body?: object): Promise<T> {
    const response = await fetch(`${endpoint}${path}`, {
      method: body ? "POST" : "GET",
      cache: "no-store",
      headers: { Authorization: `Bearer ${credential}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const payload: unknown = await response.json();
      const detail = typeof payload === "object" && payload !== null && "detail" in payload
        && typeof payload.detail === "string" ? payload.detail : "Review request failed";
      throw new Error(detail);
    }
    return await response.json() as T;
  }

  async function load(credential: string) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await request<ReviewQueue>(credential);
      setToken(credential);
      setQueue(next);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not load the review queue");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  async function decide(status: Decision) {
    if (!queue?.pair || submitting.current || error) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    const pair = queue.pair;
    try {
      await request(token, `/${pair.id}/decision`, {
        status, event_a_id: pair.event_a_id, event_b_id: pair.event_b_id,
      });
      setNotice(status === "merged" ? "Pair merged." : status === "distinct" ? "Marked distinct." : "Pair skipped.");
      setQueue(await request<ReviewQueue>(token));
      heading.current?.focus();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Decision failed. Refresh the queue to continue.");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      const target = event.target;
      if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
        || (target instanceof HTMLElement && (
          target.closest("input, textarea, select, button, a") || target.isContentEditable
        ))) return;
      const action = { m: "merged", d: "distinct", s: "skipped" }[event.key.toLowerCase()];
      if (action && queue?.pair && !busy && !error) {
        event.preventDefault();
        void decide(action as Decision);
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  });

  return (
    <main className="review-page">
      <header className="review-header">
        <div><p className="eyebrow">Admin · Event Discovery</p><h1 ref={heading} tabIndex={-1}>Dedup review</h1></div>
        <a href="/">Back to map</a>
      </header>
      {error ? <p role="alert" className="review-error">{error}</p> : null}
      {!token ? (
        <form className="review-login" onSubmit={(event) => {
          event.preventDefault();
          const credential = String(new FormData(event.currentTarget).get("token") ?? "").trim();
          if (credential) void load(credential);
        }}>
          <h2>Unlock the review queue</h2>
          <label htmlFor="review-token">Admin token</label>
          <input id="review-token" name="token" type="password" autoComplete="off" required disabled={busy} />
          <button type="submit" disabled={busy}>{busy ? "Loading…" : "Unlock"}</button>
        </form>
      ) : (
        <>
          <div className="review-toolbar">
            <p>{queue?.pending ?? 0} pending pairs</p>
            <button disabled={busy} onClick={() => void load(token)}>Refresh queue</button>
            <button disabled={busy} onClick={() => { setToken(""); setQueue(null); setError(""); setNotice(""); }}>Lock</button>
          </div>
          <p role="status" aria-live="polite">{busy ? "Saving / loading…" : notice}</p>
          {queue?.pair ? (
            <>
              <p className="review-metrics">
                Similarity {(queue.pair.similarity_score * 100).toFixed(1)}% · {queue.pair.time_delta_minutes} minutes apart · {queue.pair.distance_meters} m apart
              </p>
              <p className="review-muted">Original evidence captured when this pair entered the queue.</p>
              <section className="review-pair" aria-label="Event comparison">
                <ListingCard listing={queue.pair.snapshot_a} />
                <ListingCard listing={queue.pair.snapshot_b} />
              </section>
              <div className="review-actions" aria-label="Review actions">
                <button disabled={busy || Boolean(error)} onClick={() => void decide("merged")}>Merge (M)</button>
                <button disabled={busy || Boolean(error)} onClick={() => void decide("distinct")}>Distinct (D)</button>
                <button disabled={busy || Boolean(error)} onClick={() => void decide("skipped")}>Skip (S)</button>
              </div>
              <p className="review-muted">Merge combines these events. Distinct and Skip keep them separate.</p>
            </>
          ) : <h2>No pending pairs. You’re all caught up.</h2>}
        </>
      )}
    </main>
  );
}
