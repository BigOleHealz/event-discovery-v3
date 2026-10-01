import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FriendsPanel } from "./FriendsPanel";
import { FriendsGoing } from "./FriendsGoing";
import { useFriendsLayer } from "./useFriendsLayer";
import { friendsChanged } from "./friends";

const person = { id: "friend", display_name: "Sam", avatar_url: "https://images.example.test/sam.png" };
const viewport = { north: 40, south: 39, east: -75, west: -76, zoom: 14 };
const filters = { startsAfter: null, startsBefore: null, timeOfDayStart: "", timeOfDayEnd: "", categories: [] };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("requires an explicit friend accept and permits removal", async () => {
  let accepted = false;
  let removed = false;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (url.endsWith("/accept")) accepted = true;
      if (url.endsWith("/remove")) removed = true;
      return new Response(null, { status: 204 });
    }
    return Response.json(removed ? [] : [{ ...person, status: accepted ? "accepted" : "incoming", created_at: "2050-01-01" }]);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<FriendsPanel apiBaseUrl="https://api.example.test" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Accept friend request" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove friend" }));
  await waitFor(() => expect(screen.queryByText("Sam")).toBeNull());
  expect(fetcher.mock.calls.every(([, init]) => init?.credentials === "include" && init.cache === "no-store")).toBe(true);
});

it("renders friend avatars and clears attendance after a failed refresh", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json([person])).mockResolvedValue(new Response(null, { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  render(<FriendsGoing apiBaseUrl="https://api.example.test" eventId="event" />);
  await screen.findByText("Sam");
  expect(document.querySelector("img")).toHaveAttribute("referrerpolicy", "no-referrer");
  act(() => friendsChanged());
  await screen.findByText("Friends unavailable. Please try again.");
  expect(screen.queryByText("Sam")).toBeNull();
});

it("clears badges on sign-out, toggle off, stale events, and rejects late responses", async () => {
  let resolve: (value: Response) => void = () => {};
  const fetcher = vi.fn().mockResolvedValue(Response.json({ events: [{ event_id: "event", friends: [person] }], cells: [] }));
  vi.stubGlobal("fetch", fetcher);
  const { result, rerender } = renderHook(({ user, enabled, stale }) =>
    useFriendsLayer("https://api.example.test", user, enabled, viewport, filters, stale),
  { initialProps: { user: "alice" as string | null, enabled: true, stale: false } });
  await waitFor(() => expect(result.current.badges.get("event")).toBe(1));
  rerender({ user: "alice", enabled: false, stale: false });
  expect(result.current.badges.size).toBe(0);
  fetcher.mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
  rerender({ user: "bob", enabled: true, stale: false });
  rerender({ user: null, enabled: true, stale: false });
  await act(async () => resolve(Response.json({ events: [{ event_id: "private", friends: [person] }], cells: [] })));
  expect(result.current.badges.size).toBe(0);
  rerender({ user: "alice", enabled: true, stale: true });
  expect(result.current.badges.size).toBe(0);
});

it("uses the current viewport and filters for aggregated friends badges", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ events: [], cells: [{ cell_id: "cell:12:test", event_count: 2 }] }));
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useFriendsLayer("https://api.example.test", "alice", true,
    { ...viewport, zoom: 12 }, { ...filters, categories: ["music"] }, false));
  await waitFor(() => expect(result.current.badges.get("cell:12:test")).toBe(2));
  const url = new URL(fetcher.mock.calls[0][0] as string);
  expect(url.pathname).toBe("/api/events/friends");
  expect(url.searchParams.get("zoom")).toBe("12");
  expect(url.searchParams.get("categories")).toBe("music");
});
