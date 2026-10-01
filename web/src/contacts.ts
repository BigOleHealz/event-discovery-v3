export interface Contact {
  id: string;
  display_name: string | null;
  email: string | null;
  phone_e164: string | null;
  matched_user_id: string | null;
}

export async function contactsRequest(base: string, path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(`${base.replace(/\/$/, "")}/api/contacts${path}`, {
    ...init, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Sign in again.");
    if (response.status === 422) throw new Error("Use a vCard with valid emails or phone numbers without extensions (up to 2,000 addresses). Numbers without a country code default to +1.");
    throw new Error("Unable to load or import contacts. Please try again.");
  }
  return response.json() as Promise<unknown>;
}

export async function listContacts(base: string, search: string, offset: number, signal: AbortSignal): Promise<Contact[]> {
  const value = await contactsRequest(base, `?limit=20&offset=${offset}&q=${encodeURIComponent(search)}`, { signal });
  if (!Array.isArray(value) || !value.every((row: unknown) => {
    if (typeof row !== "object" || row === null) return false;
    const item = row as Record<string, unknown>;
    return typeof item.id === "string" && ["display_name", "email", "phone_e164", "matched_user_id"]
      .every((key) => item[key] === null || typeof item[key] === "string");
  })) throw new Error("Invalid contacts response");
  return value as Contact[];
}
