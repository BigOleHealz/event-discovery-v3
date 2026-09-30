export interface Invite {
  id: string;
  canonical_event_id: string;
  event_title: string;
  starts_at: string;
  to_user_id: string | null;
  channel?: string;
  sms_state?: string | null;
  recipient_name: string | null;
  invited_by: string[];
  inviter_names: string[];
  status: "pending" | "accepted" | "declined";
  message: string | null;
  sent_at: string;
  responded_at: string | null;
}

function isInvite(value: unknown): value is Invite {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Record<string, unknown>;
  return ["id", "canonical_event_id", "event_title", "starts_at", "sent_at"]
    .every((key) => typeof row[key] === "string") &&
    ["recipient_name", "message", "responded_at", "to_user_id"].every((key) =>
      row[key] === null || typeof row[key] === "string") &&
    ["invited_by", "inviter_names"].every((key) =>
      Array.isArray(row[key]) && row[key].every((item: unknown) => typeof item === "string")) &&
    ["pending", "accepted", "declined"].includes(String(row.status));
}

async function inviteRequest(base: string, path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(`${base.replace(/\/$/, "")}/api/invites${path}`, {
    ...init, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Sign in again.");
    if (response.status === 422) throw new Error("Use registered emails, international +country-code phone numbers, or your imported contacts. Exclude yourself.");
    if (response.status === 503) throw new Error("SMS invitations are not configured yet.");
    if (response.status === 404) throw new Error("This invite or upcoming event is no longer available.");
    if (response.status === 409) throw new Error("This event is no longer open for responses.");
    throw new Error("Unable to update invitations. Please try again.");
  }
  return response.json() as Promise<unknown>;
}

export async function listInvites(
  base: string, kind: "received" | "sent", offset: number, signal: AbortSignal,
): Promise<Invite[]> {
  const payload = await inviteRequest(base, `/${kind}?limit=20&offset=${offset}`, { signal });
  if (!Array.isArray(payload) || !payload.every(isInvite)) throw new Error("Invalid invite response");
  return payload;
}

export async function sendInvites(
  base: string, eventId: string, emails: string[], message: string, signal: AbortSignal,
  contactIds: string[] = [], phones: string[] = [],
): Promise<Invite[]> {
  const payload = await inviteRequest(base, "", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ canonical_event_id: eventId, emails, contact_ids: contactIds, phones, message: message || null }),
  });
  if (!Array.isArray(payload) || !payload.every(isInvite)) throw new Error("Invalid invite response");
  return payload;
}

export async function respondToInvite(
  base: string, id: string, response: "accept" | "decline", signal: AbortSignal,
): Promise<Invite> {
  const payload = await inviteRequest(base, `/${encodeURIComponent(id)}/respond`, {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ response }),
  });
  if (!isInvite(payload)) throw new Error("Invalid invite response");
  return payload;
}


export async function retrySMS(base: string, id: string, signal: AbortSignal): Promise<Invite> {
  const value = await inviteRequest(base, `/${encodeURIComponent(id)}/retry-sms`, { method: "POST", signal });
  if (!isInvite(value)) throw new Error("Invalid invite response");
  return value;
}
