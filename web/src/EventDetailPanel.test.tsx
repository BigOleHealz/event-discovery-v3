import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EventDetailPanel } from "./EventDetailPanel";
import type { EventFeature } from "./events";

function event(id: string): EventFeature {
  return { type: "Feature", id, geometry: { type: "Point", coordinates: [-75, 40] },
    properties: { title: `Event ${id}`, description: null, starts_at: "2050-09-27T18:00:00Z",
      ends_at: null, timezone: "America/New_York", primary_category: "Jazz",
      venue: { id: null, name: "Jazz Club", city: "Philadelphia", formatted_address: null },
      registration_links: [{ source: "eventbrite", url: `https://fixture.test/${id}` }] } };
}
function response(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("similar events in details", () => {
  it("opens a recommended event with its own details and registration link", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response([{ event: event("b"), score: 0.8 }]))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetch);
    function Detail() {
      const [selected, select] = useState<EventFeature | null>(event("a"));
      return <EventDetailPanel apiBaseUrl="https://api.fixture.test" event={selected}
        onClose={() => select(null)} onSelect={select} />;
    }
    render(<Detail />);
    fireEvent.click(await screen.findByRole("button", { name: /Event b/ }));
    expect(screen.getByRole("dialog", { name: "Event b" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Register on Eventbrite" }))
      .toHaveAttribute("href", "https://fixture.test/b");
    await screen.findByText("No similar upcoming events found.");
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "https://api.fixture.test/api/events/a/similar", "https://api.fixture.test/api/events/b/similar",
    ]);
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({ credentials: "omit", cache: "no-store" });
  });

  it("keeps event details usable on failure and supports retry", async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetch);
    render(<EventDetailPanel apiBaseUrl="." event={event("a")} onClose={vi.fn()} onSelect={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry similar events" }));
    expect(screen.getByRole("link", { name: "Register on Eventbrite" })).toBeVisible();
    await screen.findByText("No similar upcoming events found.");
  });

  it("discards a late response when selection changes", async () => {
    let finish: (value: Response) => void = () => { throw new Error("request not started"); };
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => {
      finish = resolve;
    })).mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetch);
    const props = { apiBaseUrl: ".", onClose: vi.fn(), onSelect: vi.fn() };
    const view = render(<EventDetailPanel {...props} event={event("a")} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    view.rerender(<EventDetailPanel {...props} event={event("b")} />);
    await screen.findByText("No similar upcoming events found.");
    await act(async () => finish(response([{ event: event("stale"), score: 0.8 }])));
    expect(screen.queryByRole("button", { name: /Event stale/ })).not.toBeInTheDocument();
    expect((fetch.mock.calls[0]?.[1] as RequestInit).signal?.aborted).toBe(true);
  });

  it("rejects malformed responses without exposing unsafe registration links", async () => {
    const unsafe = event("b");
    unsafe.properties.registration_links[0]!.url = "javascript:alert(1)";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([{ event: unsafe, score: 0.8 }])));
    render(<EventDetailPanel apiBaseUrl="." event={event("a")} onClose={vi.fn()} onSelect={vi.fn()} />);
    await screen.findByRole("button", { name: "Retry similar events" });
    expect(screen.queryByRole("button", { name: /Event b/ })).not.toBeInTheDocument();
  });
});
