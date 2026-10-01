export interface FeedbackRequest {
  attendance_id: string;
  canonical_event_id: string;
  event_title: string;
  starts_at: string;
  timezone: string;
  requested_at: string;
  rating: number | null;
  feedback_text: string | null;
  feedback_at: string | null;
}

function isFeedback(value: unknown): value is FeedbackRequest {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Record<string, unknown>;
  return ["attendance_id", "canonical_event_id", "event_title", "starts_at", "timezone", "requested_at"]
    .every((key) => typeof row[key] === "string") &&
    ["feedback_text", "feedback_at"].every((key) => row[key] === null || typeof row[key] === "string") &&
    (row.rating === null || (typeof row.rating === "number" && Number.isInteger(row.rating) && row.rating >= 1 && row.rating <= 5));
}

async function feedbackRequest(base: string, path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(`${base.replace(/\/$/, "")}/api/attendance${path}`, {
    ...init, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Sign in again.");
    if (response.status === 404) throw new Error("This feedback request is no longer available.");
    if (response.status === 409) throw new Error("Feedback is only available after the event.");
    throw new Error("Unable to save feedback. Please try again.");
  }
  return response.json() as Promise<unknown>;
}

export async function listFeedback(
  base: string, completed: boolean, offset: number, signal: AbortSignal,
): Promise<FeedbackRequest[]> {
  const result = await feedbackRequest(base, `/feedback?completed=${completed}&limit=20&offset=${offset}`, { signal });
  if (!Array.isArray(result) || !result.every(isFeedback)) throw new Error("Invalid feedback response");
  return result;
}

export async function saveFeedback(
  base: string, id: string, rating: number, text: string, signal: AbortSignal,
): Promise<FeedbackRequest> {
  const result = await feedbackRequest(base, `/${encodeURIComponent(id)}/feedback`, {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating, feedback_text: text || null }),
  });
  if (!isFeedback(result)) throw new Error("Invalid feedback response");
  return result;
}
