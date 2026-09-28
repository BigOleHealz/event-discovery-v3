import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InviteForm } from "./InviteForm";
import { InviteInbox } from "./InviteInbox";
import type { Invite } from "./invites";

const invite: Invite = {
  id: "invite-one", canonical_event_id: "event-one", event_title: "Evening jazz",
  starts_at: "2050-09-28T12:00:00Z", to_user_id: "recipient", recipient_name: "Recipient",
  invited_by: ["sender"], inviter_names: ["Sender"], status: "pending", message: "Join us!",
  sent_at: "2050-09-27T12:00:00Z", responded_at: null,
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("invitation controls", () => {
  it("sends account emails and a message with credentials and no caching", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([invite]));
    vi.stubGlobal("fetch", fetch);
    render(<InviteForm apiBaseUrl="https://api.example.test" eventId="event-one" />);
    fireEvent.change(screen.getByLabelText("Friends’ account emails"), {
      target: { value: "one@example.com, two@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Message (optional)"), { target: { value: "Join us!" } });
    fireEvent.click(screen.getByRole("button", { name: "Send invites" }));
    await screen.findByText("1 invitation saved. Check Sent for responses.");
    expect(fetch).toHaveBeenCalledWith("https://api.example.test/api/invites", expect.objectContaining({
      method: "POST", credentials: "include", cache: "no-store",
      body: JSON.stringify({ canonical_event_id: "event-one", emails: ["one@example.com", "two@example.com"], message: "Join us!" }),
    }));
    expect(screen.getByLabelText("Friends’ account emails")).toHaveValue("");
  });

  it("keeps the draft when an unmatched account is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 422 })));
    render(<InviteForm apiBaseUrl="https://api.example.test" eventId="event-one" />);
    fireEvent.change(screen.getByLabelText("Friends’ account emails"), { target: { value: "unknown@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send invites" }));
    await screen.findByText("Use registered account emails, excluding your own.");
    expect(screen.getByLabelText("Friends’ account emails")).toHaveValue("unknown@example.com");
    expect(screen.getByRole("button", { name: "Send invites" })).toBeEnabled();
  });

  it("accepts a received invite and displays sent responses without recipient actions", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json([invite]))
      .mockResolvedValueOnce(Response.json({ ...invite, status: "accepted" }))
      .mockResolvedValueOnce(Response.json([{ ...invite, status: "accepted" }]));
    vi.stubGlobal("fetch", fetch);
    render(<InviteInbox apiBaseUrl="https://api.example.test" onClose={vi.fn()} />);
    const inbox = within(screen.getByRole("dialog", { name: "Invitations" }));
    await inbox.findByText("From Sender");
    fireEvent.click(inbox.getByRole("button", { name: "Accept" }));
    await inbox.findByText("Accepted — you’re going");
    expect(fetch).toHaveBeenNthCalledWith(2, "https://api.example.test/api/invites/invite-one/respond", expect.objectContaining({
      method: "POST", body: JSON.stringify({ response: "accept" }), credentials: "include", cache: "no-store",
    }));
    expect(inbox.getByRole("button", { name: "Accept" })).toBeDisabled();
    fireEvent.click(inbox.getByRole("button", { name: "Sent" }));
    await inbox.findByText("To Recipient");
    expect(inbox.getByText("Accepted", { exact: true })).toBeVisible();
    expect(inbox.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("clears private results on a failed reload and reports expiry", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json([invite]))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    const close = vi.fn();
    render(<InviteInbox apiBaseUrl="https://api.example.test" onClose={close} />);
    await screen.findByText("Evening jazz");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Your session expired. Sign in again.");
    expect(screen.queryByText("Evening jazz")).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Invitations" }), { key: "Escape" });
    await waitFor(() => expect(close).toHaveBeenCalled());
  });
});
