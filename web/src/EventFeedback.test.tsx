import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventFeedback } from "./EventFeedback";
import type { FeedbackRequest } from "./feedback";

const request: FeedbackRequest = {
  attendance_id: "attendance-one", canonical_event_id: "event-one", event_title: "Yesterday’s show",
  starts_at: "2026-09-27T12:00:00Z", timezone: "America/New_York", requested_at: "2026-09-28T10:00:00Z",
  rating: null, feedback_text: null, feedback_at: null,
};
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("event feedback", () => {
  it("requires a rating and saves rating/text through the authenticated endpoint", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json([request]))
      .mockResolvedValueOnce(Response.json({ ...request, rating: 4, feedback_text: "Good show.", feedback_at: "2026-09-28T12:00:00Z" }));
    vi.stubGlobal("fetch", fetch);
    render(<EventFeedback apiBaseUrl="https://api.example.test" onClose={vi.fn()} />);
    await screen.findByText("Yesterday’s show");
    expect(screen.getByRole("button", { name: "Save feedback" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "4 stars" }));
    fireEvent.change(screen.getByLabelText("Tell us more (optional)"), { target: { value: "Good show." } });
    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    await screen.findByText("Feedback saved. Thank you!");
    expect(fetch).toHaveBeenLastCalledWith("https://api.example.test/api/attendance/attendance-one/feedback", expect.objectContaining({
      credentials: "include", cache: "no-store", method: "POST",
      body: JSON.stringify({ rating: 4, feedback_text: "Good show." }),
    }));
  });

  it("loads reviewed feedback for editing", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(Response.json([{ ...request, rating: 3, feedback_text: "Too loud.", feedback_at: "2026-09-28T12:00:00Z" }]));
    vi.stubGlobal("fetch", fetch);
    render(<EventFeedback apiBaseUrl="https://api.example.test" onClose={vi.fn()} />);
    await screen.findByText("No events here yet.");
    fireEvent.click(screen.getByRole("button", { name: "Reviewed" }));
    await screen.findByText("Yesterday’s show");
    expect(screen.getByRole("radio", { name: "3 stars" })).toBeChecked();
    expect(screen.getByLabelText("Tell us more (optional)")).toHaveValue("Too loud.");
    expect(fetch).toHaveBeenLastCalledWith("https://api.example.test/api/attendance/feedback?completed=true&limit=20&offset=0", expect.objectContaining({
      credentials: "include", cache: "no-store",
    }));
  });

  it("keeps the draft and reports submission failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json([request]))
      .mockResolvedValueOnce(new Response(null, { status: 409 })));
    render(<EventFeedback apiBaseUrl="https://api.example.test" onClose={vi.fn()} />);
    await screen.findByText("Yesterday’s show");
    fireEvent.click(screen.getByRole("radio", { name: "5 stars" }));
    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    await screen.findByText("Feedback is only available after the event.");
    expect(screen.getByRole("radio", { name: "5 stars" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save feedback" })).toBeEnabled();
  });

  it("clears results on expiry and supports closing with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json([request]))
      .mockResolvedValueOnce(new Response(null, { status: 401 })));
    const close = vi.fn();
    render(<EventFeedback apiBaseUrl="https://api.example.test" onClose={close} />);
    await screen.findByText("Yesterday’s show");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Your session expired. Sign in again.");
    expect(screen.queryByText("Yesterday’s show")).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Event feedback" }), { key: "Escape" });
    expect(close).toHaveBeenCalled();
  });
});
