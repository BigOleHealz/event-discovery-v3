import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EVENT_CACHE_NAME, fetchEvents } from "./events";
import type { EventFilters, EventViewport } from "./events";

const viewport: EventViewport = { north: 40.1, south: 39.8, east: -74.9, west: -75.3, zoom: 13 };
const filters: EventFilters = {
  startsAfter: "2026-09-01T00:00:00Z", startsBefore: "2026-09-08T00:00:00Z",
  timeOfDayStart: "18:00", timeOfDayEnd: "23:00", categories: ["music", "science"],
};
const empty = { type: "FeatureCollection", features: [] };
const cells = {
  type: "FeatureCollection",
  features: [{ type: "Feature", id: "cell", geometry: { type: "Point", coordinates: [-75, 40] },
    properties: { count: 2, top_categories: ["music"] } }],
};
const stored = new Map<string, Response>();
const cache = {
  match: vi.fn(async (url: string) => stored.get(url)?.clone()),
  put: vi.fn(async (url: string, response: Response) => { stored.set(url, response.clone()); }),
  keys: vi.fn(async () => [...stored.keys()]),
  delete: vi.fn(async (url: string) => stored.delete(url)),
};
const load = (bounds = viewport, selected = filters, signal = new AbortController().signal) =>
  fetchEvents("https://api.example.test", signal, bounds, selected);

beforeEach(() => {
  stored.clear();
  vi.stubGlobal("caches", { open: vi.fn().mockResolvedValue(cache) });
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify(cells))));
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("public event network-first cache", () => {
  it("populates, replays the exact request, then replaces it with a successful empty response", async () => {
    expect(await load()).toEqual({ features: cells.features, stale: false });
    expect(caches.open).toHaveBeenCalledWith(EVENT_CACHE_NAME);
    expect(fetch).toHaveBeenCalledWith(expect.any(URL), expect.objectContaining({
      credentials: "omit", cache: "no-store",
    }));
    const url = new URL([...stored.keys()][0]!);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      north: "40.1", south: "39.8", east: "-74.9", west: "-75.3", zoom: "13",
      starts_after: filters.startsAfter, starts_before: filters.startsBefore,
      time_of_day_start: "18:00", time_of_day_end: "23:00", categories: "music,science",
    });
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    expect(await load()).toEqual({ features: cells.features, stale: true });
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(empty)));
    expect(await load()).toEqual({ features: [], stale: false });
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    expect(await load()).toEqual({ features: [], stale: true });
  });

  it.each(["north", "south", "east", "west", "zoom"] as const)("never substitutes cached %s", async (key) => {
    await load();
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    await expect(load({ ...viewport, [key]: viewport[key] + 1 })).rejects.toThrow("no saved events");
  });

  it.each([
    { startsAfter: "2026-09-02T00:00:00Z" }, { startsBefore: null },
    { timeOfDayStart: "19:00" }, { timeOfDayEnd: "" }, { categories: ["music"] },
  ])("never substitutes cached filters: %j", async (change) => {
    await load();
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    await expect(load(viewport, { ...filters, ...change })).rejects.toThrow("no saved events");
  });

  it("does not overwrite good data with HTTP errors or malformed payloads", async () => {
    await load();
    vi.mocked(fetch).mockResolvedValue(new Response("unavailable", { status: 503 }));
    await expect(load()).rejects.toThrow("503");
    vi.mocked(fetch).mockResolvedValue(new Response('{"type":"FeatureCollection","features":[{}]}'));
    await expect(load()).rejects.toThrow("GeoJSON");
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    expect((await load()).features).toEqual(cells.features);
    expect(cache.put).toHaveBeenCalledTimes(1);
  });

  it("replays the matching cache if the connection drops while reading the body", async () => {
    await load();
    vi.mocked(fetch).mockResolvedValue(new Response(new ReadableStream({
      start(controller) { controller.error(new TypeError("connection interrupted")); },
    })));
    expect(await load()).toEqual({ features: cells.features, stale: true });
    expect(cache.put).toHaveBeenCalledTimes(1);
  });

  it("does not fall back on abort, and tolerates unavailable storage online", async () => {
    await load();
    const controller = new AbortController();
    controller.abort();
    vi.mocked(fetch).mockRejectedValue(controller.signal.reason);
    await expect(load(viewport, filters, controller.signal)).rejects.toThrow("aborted");
    expect(cache.match).not.toHaveBeenCalled();
    vi.mocked(caches.open).mockRejectedValue(new Error("quota"));
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(empty)));
    expect(await load()).toEqual({ features: [], stale: false });
  });

  it("bounds storage and stores no network response headers", async () => {
    for (let zoom = 0; zoom < 51; zoom++) {
      vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(empty), {
        headers: { "X-Secret": "not-cacheable" },
      }));
      await load({ ...viewport, zoom });
    }
    expect(stored.size).toBe(50);
    expect([...stored.values()].every((response) => !response.headers.has("X-Secret"))).toBe(true);
  });
});
