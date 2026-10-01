import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ContactList } from "./ContactList";
import { ContactsPanel } from "./ContactsPanel";
import { InviteForm } from "./InviteForm";

const contact = { id: "contact", display_name: "Registered Friend", phone_e164: "+14155552671", email: null, matched_user_id: "recipient" };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("selects an imported contact and sends its id with an account email", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json([contact]))
    .mockResolvedValueOnce(Response.json([]));
  vi.stubGlobal("fetch", fetch);
  render(<InviteForm apiBaseUrl="https://api.test" eventId="event" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose contacts (0 selected)" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("Friends’ account emails"), { target: { value: "friend@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Send invites" }));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith("https://api.test/api/invites", expect.objectContaining({
    credentials: "include", cache: "no-store", method: "POST",
    body: JSON.stringify({ canonical_event_id: "event", emails: ["friend@example.com"], contact_ids: ["contact"], message: null }),
  })));
});

it("keeps contact identities private and clears stale results after auth failure", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json([contact]))
    .mockResolvedValueOnce(new Response(null, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  render(<ContactList apiBaseUrl="https://api.test" />);
  await screen.findByText("Registered Friend");
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/contacts?"), expect.objectContaining({ credentials: "include", cache: "no-store" }));
  fireEvent.change(screen.getByLabelText("Search contacts"), { target: { value: "x" } });
  await screen.findByText("Your session expired. Sign in again.");
  expect(screen.queryByText("Registered Friend")).not.toBeInTheDocument();
});

it("imports a vCard and refreshes the list", async () => {
  const fetch = vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith("/import") ? { imported: 1 } : [contact])));
  vi.stubGlobal("fetch", fetch);
  const close = vi.fn();
  render(<ContactsPanel apiBaseUrl="https://api.test" onClose={close} />);
  expect(screen.getByText("Import iPhone/iCloud contacts")).toBeVisible();
  expect(screen.getByRole("link", { name: "Apple’s contact export instructions", hidden: true }))
    .toHaveAttribute("href", "https://support.apple.com/guide/iphone/export-contacts-iph075ddebf2/ios");
  expect(screen.getByText(/Numbers without a country code default to \+1/)).toBeVisible();
  const file = new File(["BEGIN:VCARD\nEND:VCARD"], "friends.vcf", { type: "text/vcard" });
  Object.defineProperty(file, "text", { value: () => Promise.resolve("BEGIN:VCARD\nEND:VCARD") });
  fireEvent.change(screen.getByLabelText("Import vCard (.vcf)"), { target: { files: [file] } });
  await screen.findByText("Imported 1 contact addresses.");
  await screen.findByText("Registered Friend");
  expect(fetch).toHaveBeenCalledWith("https://api.test/api/contacts/import", expect.objectContaining({
    method: "POST", credentials: "include", cache: "no-store", body: JSON.stringify({ vcard: "BEGIN:VCARD\nEND:VCARD" }),
  }));
  expect(screen.getByRole("link", { name: "Import Google Contacts" })).toHaveAttribute("href", "https://api.test/api/contacts/google/start");
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});

it("disables unmatched phone and email contacts", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([
    { ...contact, id: "phone", matched_user_id: null },
    { ...contact, id: "email", phone_e164: null, email: "unknown@example.com", matched_user_id: null },
  ]));
  vi.stubGlobal("fetch", fetch);
  const toggle = vi.fn();
  render(<ContactList apiBaseUrl="https://api.test" selected={[]} onToggle={toggle} />);
  const checkboxes = await screen.findAllByRole("checkbox");
  expect(checkboxes).toHaveLength(2);
  for (const checkbox of checkboxes) expect(checkbox).toBeDisabled();
  expect(screen.getAllByText(/No matching account/)).toHaveLength(2);
  expect(toggle).not.toHaveBeenCalled();
});
