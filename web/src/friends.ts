export interface FriendPerson { id: string; display_name: string; avatar_url: string | null }
export interface Friendship extends FriendPerson { status: "incoming" | "outgoing" | "accepted"; created_at: string }
export interface FriendsLayer {
  events: { event_id: string; friends: FriendPerson[] }[];
  cells: { cell_id: string; event_count: number }[];
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
export function isPerson(value: unknown): value is FriendPerson {
  return record(value) && typeof value.id === "string" && typeof value.display_name === "string" &&
    (value.avatar_url === null || typeof value.avatar_url === "string");
}
export function people(value: unknown): FriendPerson[] {
  if (!Array.isArray(value) || !value.every(isPerson)) throw new Error("Invalid friends response.");
  return value;
}
export function friendships(value: unknown): Friendship[] {
  if (!Array.isArray(value) || !value.every((row: unknown) => isPerson(row) && record(row) &&
      ["incoming", "outgoing", "accepted"].includes(String(row.status)) && typeof row.created_at === "string")) {
    throw new Error("Invalid friends response.");
  }
  return value as Friendship[];
}
export function friendsLayer(value: unknown): FriendsLayer {
  if (!record(value) || !Array.isArray(value.events) || !Array.isArray(value.cells) ||
      !value.events.every((row: unknown) => record(row) && typeof row.event_id === "string" &&
        Array.isArray(row.friends) && row.friends.every(isPerson)) ||
      !value.cells.every((row: unknown) => record(row) && typeof row.cell_id === "string" &&
        typeof row.event_count === "number" && row.event_count > 0)) throw new Error("Invalid friends response.");
  return value as unknown as FriendsLayer;
}

export async function friendsRequest(url: string | URL, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(url, { ...init, credentials: "include", cache: "no-store", referrerPolicy: "no-referrer" });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Sign in again.");
    if (response.status === 422) throw new Error("Enter another registered user's email.");
    if (response.status === 404) throw new Error("This friend request is no longer available.");
    throw new Error("Friends unavailable. Please try again.");
  }
  return response.status === 204 ? null : response.json() as Promise<unknown>;
}

export function avatarUrl(value: string | null): string | undefined {
  if (!value) return undefined;
  try { return new URL(value).protocol === "https:" ? value : undefined; } catch { return undefined; }
}

export function friendsChanged() { window.dispatchEvent(new Event("friends-changed")); }
