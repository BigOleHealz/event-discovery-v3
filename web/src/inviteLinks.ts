export interface CreatedLink { id: string; url: string; expires_at: string }
export interface LinkPreview {
  canonical_event_id: string; event_title: string; starts_at: string; timezone: string;
  inviter_name: string; message: string | null; expires_at: string;
}
export interface LinkTracking {
  id: string; canonical_event_id: string; event_title: string; created_at: string;
  expires_at: string; revoked_at: string | null; first_opened_at: string | null;
  message: string | null; acceptance_count: number;
  accepted_by: { user_id: string; display_name: string; accepted_at: string }[];
}

export async function linkRequest(base: string, path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(`${base.replace(/\/$/, "")}/api/invite-links${path}`, {
    ...init, credentials: "include", cache: "no-store", referrerPolicy: "no-referrer",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Sign in with Google to continue.");
    if (response.status === 404 || response.status === 410) throw new Error("This invitation link is unavailable, expired, or revoked.");
    if (response.status === 422) throw new Error("You cannot accept your own invitation. Check your message and try again.");
    throw new Error("Unable to update this invitation. Please try again.");
  }
  return response.status === 204 ? null : response.json() as Promise<unknown>;
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null) throw new Error("Invalid invitation response");
  return value as Record<string, unknown>;
}
function strings(row: Record<string, unknown>, keys: string[]) {
  return keys.every((key) => typeof row[key] === "string");
}
export function createdLink(value: unknown): CreatedLink {
  const row = record(value);
  if (!strings(row, ["id", "url", "expires_at"])) throw new Error("Invalid invitation response");
  return row as unknown as CreatedLink;
}
export function linkPreview(value: unknown): LinkPreview {
  const row = record(value);
  if (!strings(row, ["canonical_event_id", "event_title", "starts_at", "timezone", "inviter_name", "expires_at"]) ||
      !(row.message === null || typeof row.message === "string")) throw new Error("Invalid invitation response");
  return row as unknown as LinkPreview;
}
export function linkTracking(value: unknown): LinkTracking[] {
  if (!Array.isArray(value)) throw new Error("Invalid invitation response");
  for (const item of value) {
    const row = record(item);
    if (!strings(row, ["id", "canonical_event_id", "event_title", "created_at", "expires_at"]) ||
        !["message", "revoked_at", "first_opened_at"].every((key) => row[key] === null || typeof row[key] === "string") ||
        typeof row.acceptance_count !== "number" || !Array.isArray(row.accepted_by) ||
        !row.accepted_by.every((person: unknown) => strings(record(person), ["user_id", "display_name", "accepted_at"]))) {
      throw new Error("Invalid invitation response");
    }
  }
  return value as LinkTracking[];
}

const pendingKey = "event-discovery-pending-invitation";
const tokenPattern = /^[A-Za-z0-9_-]{43}$/;
export function pendingInvitation(): string | null {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const incoming = fragment.get("invite");
  if (incoming !== null) {
    // Remove the bearer from visible history before loading third-party resources.
    history.replaceState(null, "", window.location.pathname + window.location.search);
    if (!tokenPattern.test(incoming)) {
      sessionStorage.removeItem(pendingKey);
      return null;
    }
    sessionStorage.setItem(pendingKey, incoming);
    return incoming;
  }
  const saved = sessionStorage.getItem(pendingKey);
  return saved && tokenPattern.test(saved) ? saved : null;
}
export function forgetInvitation() { sessionStorage.removeItem(pendingKey); }
