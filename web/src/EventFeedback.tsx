import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { listFeedback, saveFeedback, type FeedbackRequest } from "./feedback";

function FeedbackCard({ item, apiBaseUrl }: { item: FeedbackRequest; apiBaseUrl: string }) {
  const [rating, setRating] = useState(item.rating ?? 0);
  const [text, setText] = useState(item.feedback_text ?? "");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(item.feedback_at ? "Feedback saved." : "");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function submit() {
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setStatus("");
    try {
      await saveFeedback(apiBaseUrl, item.attendance_id, rating, text, controller.signal);
      if (!controller.signal.aborted) setStatus("Feedback saved. Thank you!");
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setStatus(reason instanceof Error ? reason.message : "Unable to save feedback.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <li className="feedback-card">
    <h3>{item.event_title}</h3>
    <time dateTime={item.starts_at}>{new Date(item.starts_at).toLocaleString(undefined, {
      dateStyle: "medium", timeStyle: "short", timeZone: item.timezone,
    })}</time>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={busy}>
        <legend>How was it?</legend>
        <div className="feedback-ratings">{[1, 2, 3, 4, 5].map((value) => <label key={value}>
          <input type="radio" name={`rating-${item.attendance_id}`} value={value} required
            checked={rating === value} onChange={() => setRating(value)} />
          {value} {value === 1 ? "star" : "stars"}
        </label>)}</div>
      </fieldset>
      <label>Tell us more (optional)
        <textarea value={text} maxLength={2000} disabled={busy} onChange={(event) => setText(event.target.value)} />
      </label>
      <button type="submit" disabled={busy || rating === 0}>{busy ? "Saving…" : "Save feedback"}</button>
    </form>
    {status && <p role="status">{status}</p>}
  </li>;
}

export function EventFeedback({ apiBaseUrl, onClose }: { apiBaseUrl: string; onClose: () => void }) {
  const [completed, setCompleted] = useState(false);
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [items, setItems] = useState<FeedbackRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const panel = useRef<HTMLElement>(null);
  useEffect(() => { panel.current?.focus(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      try {
        const result = await listFeedback(apiBaseUrl, completed, offset, controller.signal);
        if (!controller.signal.aborted) setItems(result);
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setItems([]);
          setError(reason instanceof Error ? reason.message : "Unable to load feedback requests.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [apiBaseUrl, completed, offset, version]);

  return createPortal(<aside className="invite-inbox feedback-inbox" role="dialog" aria-modal="false"
    aria-label="Event feedback" ref={panel} tabIndex={-1}
    onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
    <button type="button" onClick={onClose} aria-label="Close event feedback">Close</button>
    <h2>Event feedback</h2>
    <p>How were the events you went to?</p>
    <nav aria-label="Feedback lists">
      <button type="button" aria-pressed={!completed} onClick={() => { setCompleted(false); setOffset(0); }}>To review</button>
      <button type="button" aria-pressed={completed} onClick={() => { setCompleted(true); setOffset(0); }}>Reviewed</button>
      <button type="button" disabled={loading} onClick={() => setVersion((value) => value + 1)}>Refresh</button>
    </nav>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading feedback requests…</p> : items.length === 0 ? <p>No events here yet.</p> :
      <ul>{items.map((item) => <FeedbackCard key={`${item.attendance_id}:${item.feedback_at}`} item={item} apiBaseUrl={apiBaseUrl} />)}</ul>}
    <nav aria-label="Feedback pages">
      <button type="button" disabled={loading || offset === 0} onClick={() => setOffset((value) => Math.max(0, value - 20))}>Previous</button>
      <button type="button" disabled={loading || items.length < 20} onClick={() => setOffset((value) => value + 20)}>Next</button>
    </nav>
  </aside>, document.body);
}
