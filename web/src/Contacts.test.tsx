import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ContactList } from "./ContactList";
import { ContactsPanel } from "./ContactsPanel";
import { InviteForm } from "./InviteForm";

const contact = { id: "contact", display_name: "SMS Friend", phone_e164: "+14155552671", email: null, matched_user_id: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("selects an imported contact and sends its id with an entered phone", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json([contact]))
    .mockResolvedValueOnce(Response.json([]));
  vi.stubGlobal("fetch", fetch);
  render(<InviteForm apiBaseUrl="https://api.test" eventId="event" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose contacts (0 selected)" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Friends’ emails or phone numbers"), { target: { value: "+12025550123" } });
  fireEvent.click(screen.getByRole("button", { name: "Send invites" }));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith("https://api.test/api/invites", expect.objectContaining({
    credentials: "include", cache: "no-store", method: "POST",
    body: JSON.stringify({ canonical_event_id: "event", emails: [], contact_ids: ["contact"], phones: ["+12025550123"], message: null }),
  })));
});

it("keeps contact identities private and clears stale results after auth failure", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json([contact]))
    .mockResolvedValueOnce(new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  render(<ContactList apiBaseUrl="https://api.test" />);
  await screen.findByText("SMS Friend");
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/contacts?"), expect.objectContaining({ credentials: "include", cache: "no-store" }));
  fireEvent.change(screen.getByLabelText("Search contacts"), { target: { value: "x" } });
  await screen.findByText("Your session expired. Sign in again.");
  expect(screen.queryByText("SMS Friend")).not.toBeInTheDocument();
});

it("imports a vCard and refreshes the list", async () => {
  const fetch = vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith("/import") ? { imported: 1 } : [contact])));
  vi.stubGlobal("fetch", fetch);
  const close = vi.fn();
  render(<ContactsPanel apiBaseUrl="https://api.test" onClose={close} />);
  const file = new File(["BEGIN:VCARD\nEND:VCARD"], "friends.vcf", { type: "text/vcard" });
  Object.defineProperty(file, "text", { value: () => Promise.resolve("BEGIN:VCARD\nEND:VCARD") });
  fireEvent.change(screen.getByLabelText("Import vCard (.vcf)"), { target: { files: [file] } });
  await screen.findByText("Imported 1 contact addresses.");
  await screen.findByText("SMS Friend");
  expect(fetch).toHaveBeenCalledWith("https://api.test/api/contacts/import", expect.objectContaining({
    method: "POST", credentials: "include", cache: "no-store", body: JSON.stringify({ vcard: "BEGIN:VCARD\nEND:VCARD" }),
  }));
  expect(screen.getByRole("link", { name: "Import Google Contacts" })).toHaveAttribute("href", "https://api.test/api/contacts/google/start");
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
