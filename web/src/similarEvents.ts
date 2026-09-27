import { isEventFeature, type EventFeature } from "./events";

export interface SimilarEvent {
  event: EventFeature;
  score: number;
}

export async function fetchSimilarEvents(
  apiBaseUrl: string, eventId: string, signal: AbortSignal,
): Promise<SimilarEvent[]> {
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, "")}/api/events/${encodeURIComponent(eventId)}/similar`,
    { signal, credentials: "omit", cache: "no-store" },
  );
  if (!response.ok) throw new Error("Similar events are temporarily unavailable.");
  const payload: unknown = await response.json();
  if (!Array.isArray(payload) || !payload.every((item: unknown) => {
    if (typeof item !== "object" || item === null) return false;
    const row = item as Record<string, unknown>;
    return isEventFeature(row.event) && typeof row.score === "number" &&
      Number.isFinite(row.score) && row.score > 0 && row.score <= 1;
  })) throw new Error("Invalid similar events response.");
  return payload as SimilarEvent[];
}
