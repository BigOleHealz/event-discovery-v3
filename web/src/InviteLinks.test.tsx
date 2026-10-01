import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShareInvite } from "./ShareInvite";
import { InviteLinkPreview } from "./InviteLinkPreview";
import { SharedInviteLinks } from "./SharedInviteLinks";
import { forgetInvitation, pendingInvitation } from "./inviteLinks";

const token = "a".repeat(43);
const link = { id: "link", url: `https://web.test/#invite=${token}`, expires_at: "2050-09-28T12:00:00Z" };
const preview = { canonical_event_id: "event", event_title: "Jazz", starts_at: "2050-09-28T12:00:00Z",
  timezone: "UTC", inviter_name: "Friend", message: "Join us", expires_at: link.expires_at };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); sessionStorage.clear(); history.replaceState(null, "", "/"); });

it("preserves an invitation across OAuth navigation without putting it in the redirect URL", () => {
  history.replaceState(null, "", `/#invite=${token}`);
  expect(pendingInvitation()).toBe(token);
  expect(location.hash).toBe("");
  history.replaceState(null, "", "/?auth_error=1");
  expect(pendingInvitation()).toBe(token);
  forgetInvitation();
  expect(pendingInvitation()).toBeNull();
  history.replaceState(null, "", "/#invite=https://evil.test");
  expect(pendingInvitation()).toBeNull();
});

it("keeps preview read-only and requires explicit acceptance after sign-in", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(preview))
    .mockResolvedValueOnce(Response.json({ invite_id: "invite", status: "accepted" }));
  vi.stubGlobal("fetch", fetch);
  const { rerender } = render(<InviteLinkPreview apiBaseUrl="https://api.test" token={token} userId={null} onClose={vi.fn()} />);
  await screen.findByText("Jazz");
  expect(screen.queryByRole("button", { name: "Accept invitation" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in with Google to accept" }))
    .toHaveAttribute("href", "https://api.test/api/auth/google/start");
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender(<InviteLinkPreview apiBaseUrl="https://api.test" token={token} userId="recipient" onClose={vi.fn()} />);
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
  await screen.findByText("Accepted — you’re going");
  expect(fetch).toHaveBeenLastCalledWith(`https://api.test/api/invite-links/${token}/accept`, expect.objectContaining({
    method: "POST", credentials: "include", cache: "no-store", referrerPolicy: "no-referrer",
  }));
});

it("shows unavailable tokens without an accept control", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 410 })));
  render(<InviteLinkPreview apiBaseUrl="https://api.test" token={token} userId="recipient" onClose={vi.fn()} />);
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Accept invitation" })).not.toBeInTheDocument();
});

it("offers copy without native sharing and a manual fallback when clipboard access fails", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(link));
  const writeText = vi.fn().mockRejectedValue(new Error("Denied"));
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  render(<ShareInvite apiBaseUrl="https://api.test" eventId="event" />);
  fireEvent.click(screen.getByRole("button", { name: "Share invite" }));
  fireEvent.click(screen.getByRole("button", { name: "Create link" }));
  await screen.findByText("Link created. Choose how to share it.");
  expect(screen.queryByRole("button", { name: "Share link" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByText("Select the link below and copy it manually.");
  expect(screen.getByLabelText("Invitation link")).toHaveValue(link.url);
  expect(writeText).toHaveBeenCalledWith(link.url);
  expect(fetch).toHaveBeenCalledOnce();
});

it("does not claim delivery after a cancelled or completed native share", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(link)));
  const share = vi.fn().mockRejectedValueOnce(new DOMException("Cancelled", "AbortError"))
    .mockResolvedValueOnce(undefined);
  vi.stubGlobal("navigator", { share });
  render(<ShareInvite apiBaseUrl="https://api.test" eventId="event" />);
  fireEvent.click(screen.getByRole("button", { name: "Share invite" }));
  fireEvent.click(screen.getByRole("button", { name: "Create link" }));
  fireEvent.click(await screen.findByRole("button", { name: "Share link" }));
  await screen.findByText("Sharing cancelled. Your link is still available.");
  fireEvent.click(screen.getByRole("button", { name: "Share link" }));
  await screen.findByText("Share sheet closed. Check Share links for acceptances.");
  expect(share).toHaveBeenCalledWith({ title: "Join me at this event", url: link.url });
});

it("tracks observed opens and named acceptances, then revokes the creator's link", async () => {
  const tracked = { ...link, canonical_event_id: "event", event_title: "Jazz", created_at: link.expires_at,
    revoked_at: null, first_opened_at: link.expires_at, message: null, acceptance_count: 1,
    accepted_by: [{ user_id: "recipient", display_name: "Sam", accepted_at: link.expires_at }] };
  const fetch = vi.fn().mockResolvedValueOnce(Response.json([tracked]))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json([{ ...tracked, revoked_at: link.expires_at }]));
  vi.stubGlobal("fetch", fetch);
  render(<SharedInviteLinks apiBaseUrl="https://api.test" />);
  await screen.findByText("Accepted: 1");
  expect(screen.getByText(/Sam — accepted/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Revoke link" }));
  await screen.findByText("Revoked");
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("https://api.test/api/invite-links/link/revoke",
    expect.objectContaining({ method: "POST", cache: "no-store", credentials: "include" })));
});
