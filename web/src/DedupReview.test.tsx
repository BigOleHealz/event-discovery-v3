import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DedupReview } from "./DedupReview";

const listing = {
  listing_id: "listing-a", title: "Jazz night", description: "An original description",
  starts_at: "2026-10-06T23:30:00Z", ends_at: null, timezone: "America/New_York",
  venue: "The Jazz Room", address: "123 Main Street", source: "eventbrite",
  url: "https://eventbrite.test/jazz",
};
const pair = {
  id: "pair-a", snapshot_a: listing,
  snapshot_b: { ...listing, listing_id: "listing-b", title: "Evening jazz", source: "meetup" },
  similarity_score: 0.8, time_delta_minutes: 15, distance_meters: 100,
  event_a_id: "event-a", event_b_id: "event-b",
};
const response = (body: object, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

async function unlock() {
  fireEvent.change(screen.getByLabelText("Admin token"), { target: { value: "test-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Unlock" }));
  await screen.findByRole("heading", { name: "Jazz night" });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("DedupReview", () => {
  it("requires a token and shows both original listings and metrics", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ pending: 1, pair }));
    vi.stubGlobal("fetch", fetcher);
    render(<DedupReview apiBaseUrl="." />);
    expect(fetcher).not.toHaveBeenCalled();
    await unlock();
    expect(screen.getByRole("heading", { name: "Evening jazz" })).toBeVisible();
    expect(screen.getAllByText("The Jazz Room")).toHaveLength(2);
    expect(screen.getByText(/15 minutes apart/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open eventbrite listing" })).toHaveAttribute("target", "_blank");
    expect(fetcher.mock.calls[0]?.[1].headers.Authorization).toBe("Bearer test-secret");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Lock" }));
    expect(screen.getByLabelText("Admin token")).toHaveValue("");
    expect(screen.queryByText("Jazz night")).not.toBeInTheDocument();
  });

  it.each([["m", "merged"], ["d", "distinct"], ["s", "skipped"]])(
    "supports %s shortcut and advances the queue", async (key, status) => {
      const fetcher = vi.fn()
        .mockResolvedValueOnce(response({ pending: 1, pair }))
        .mockResolvedValueOnce(response({ status }))
        .mockResolvedValueOnce(response({ pending: 0, pair: null }));
      vi.stubGlobal("fetch", fetcher);
      render(<DedupReview apiBaseUrl="." />);
      await unlock();
      fireEvent.keyDown(window, { key });
      fireEvent.keyDown(window, { key, repeat: true });
      await screen.findByText(/all caught up/);
      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(JSON.parse(fetcher.mock.calls[1]?.[1].body)).toEqual({
        status, event_a_id: "event-a", event_b_id: "event-b",
      });
    },
  );

  it("preserves the pair after an error and requires refresh before another decision", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({ pending: 1, pair }))
      .mockResolvedValueOnce(response({ detail: "Pair changed; refresh" }, 409))
      .mockResolvedValueOnce(response({ pending: 0, pair: null }));
    vi.stubGlobal("fetch", fetcher);
    render(<DedupReview apiBaseUrl="." />);
    await unlock();
    fireEvent.click(screen.getByRole("button", { name: "Merge (M)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pair changed; refresh");
    expect(screen.getByRole("button", { name: "Merge (M)" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "s" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh queue" }));
    await screen.findByText(/all caught up/);
  });

  it("ignores shortcuts in inputs, on buttons, and with modifiers", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(response({ pending: 1, pair })));
    vi.stubGlobal("fetch", fetcher);
    render(<DedupReview apiBaseUrl="." />);
    fireEvent.keyDown(screen.getByLabelText("Admin token"), { key: "m" });
    expect(fetcher).not.toHaveBeenCalled();
    await unlock();
    fireEvent.keyDown(screen.getByRole("button", { name: "Skip (S)" }), { key: "m" });
    fireEvent.keyDown(window, { key: "m", ctrlKey: true });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  });

  it("shows authentication failures without exposing any pair", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: "Admin token required" }, 401)));
    render(<DedupReview apiBaseUrl="." />);
    fireEvent.change(screen.getByLabelText("Admin token"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Admin token required");
    expect(screen.queryByLabelText("Event comparison")).not.toBeInTheDocument();
  });
});
