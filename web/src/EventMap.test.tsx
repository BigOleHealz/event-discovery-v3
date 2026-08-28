import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EventFeatureCollection } from "./events";
import { EventMap } from "./EventMap";

const mapConstructor = vi.fn();
const markerConstructor = vi.fn();
const pinConstructor = vi.fn();
const markerInstances: FakeMarker[] = [];
const mapInstances: FakeMap[] = [];
let viewport = { north: 40.1, south: 39.8, east: -74.9, west: -75.3 };

class FakeMap {
  private readonly listeners = new Map<string, () => void>();

  constructor(element: HTMLElement, options: google.maps.MapOptions) {
    mapConstructor(element, options);
    mapInstances.push(this);
  }

  addListener(eventName: string, handler: () => void): google.maps.MapsEventListener {
    this.listeners.set(eventName, handler);
    return {
      remove: () => this.listeners.delete(eventName),
    };
  }

  getBounds(): google.maps.LatLngBounds {
    return {
      getNorthEast: () =>
        ({ lat: () => viewport.north, lng: () => viewport.east }) as google.maps.LatLng,
      getSouthWest: () =>
        ({ lat: () => viewport.south, lng: () => viewport.west }) as google.maps.LatLng,
    } as google.maps.LatLngBounds;
  }

  trigger(eventName: string): void {
    this.listeners.get(eventName)?.();
  }
}

class FakeMarker {
  dataset: Record<string, string> = {};
  map: FakeMap | null;
  append = vi.fn();
  private readonly listeners = new Map<string, () => void>();

  constructor(options: google.maps.marker.AdvancedMarkerElementOptions) {
    this.map = (options.map as FakeMap | undefined) ?? null;
    markerConstructor(options);
    markerInstances.push(this);
  }

  addListener(eventName: string, handler: () => void): google.maps.MapsEventListener {
    this.listeners.set(eventName, handler);
    return {
      remove: () => this.listeners.delete(eventName),
    };
  }

  trigger(eventName: string): void {
    this.listeners.get(eventName)?.();
  }
}

class FakePin {
  constructor(options: google.maps.marker.PinElementOptions) {
    pinConstructor(options);
  }
}

vi.mock("@googlemaps/js-api-loader", () => ({
  setOptions: vi.fn(),
  importLibrary: vi.fn((name: string) => {
    if (name === "maps") {
      return Promise.resolve({ Map: FakeMap });
    }
    return Promise.resolve({ AdvancedMarkerElement: FakeMarker, PinElement: FakePin });
  }),
}));

const events: EventFeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "event-1",
      geometry: { type: "Point", coordinates: [-75.1809, 39.9656] },
      properties: {
        title: "Parkway Jazz Night",
        description: null,
        starts_at: "2026-09-04T23:00:00Z",
        ends_at: null,
        timezone: "America/New_York",
        primary_category: "music",
        venue: { id: null, name: null, formatted_address: null, city: "Philadelphia" },
        registration_links: [
          {
            source: "eventbrite",
            url: "https://www.eventbrite.com/e/parkway-jazz-night",
          },
        ],
      },
    },
    {
      type: "Feature",
      id: "event-2",
      geometry: { type: "Point", coordinates: [-75.1731, 39.9582] },
      properties: {
        title: "Science After Hours",
        description: null,
        starts_at: "2026-09-05T22:30:00Z",
        ends_at: null,
        timezone: "America/New_York",
        primary_category: "science",
        venue: { id: null, name: null, formatted_address: null, city: "Philadelphia" },
        registration_links: [],
      },
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  markerInstances.length = 0;
  mapInstances.length = 0;
  viewport = { north: 40.1, south: 39.8, east: -74.9, west: -75.3 };
});

function stubEventResponse(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(events), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

describe("EventMap", () => {
  it("renders one longitude-correct Google marker per event", async () => {
    stubEventResponse();

    render(<EventMap apiBaseUrl="https://api.example.test" apiKey="test-key" mapId="map-id" />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("2 events"));
    expect(mapConstructor).toHaveBeenCalledOnce();
    expect(markerConstructor).toHaveBeenCalledTimes(2);
    expect(markerConstructor).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        position: { lat: 39.9656, lng: -75.1809 },
        title: "Parkway Jazz Night",
      }),
    );
    expect(pinConstructor).toHaveBeenCalledTimes(2);
  });

  it("opens an accessible detail slide-over with the source registration link", async () => {
    stubEventResponse();
    render(<EventMap apiBaseUrl="https://api.example.test" apiKey="test-key" mapId="map-id" />);

    await waitFor(() => expect(markerInstances).toHaveLength(2));
    act(() => markerInstances[0]?.trigger("click"));

    const detail = screen.getByRole("dialog", { name: "Parkway Jazz Night" });
    expect(detail).toBeVisible();
    expect(detail.querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-09-04T23:00:00Z",
    );
    expect(screen.getByRole("link", { name: "Register on Eventbrite" })).toHaveAttribute(
      "href",
      "https://www.eventbrite.com/e/parkway-jazz-night",
    );

    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("debounces map idle events into one bounded viewport refetch", async () => {
    stubEventResponse();
    render(<EventMap apiBaseUrl="." apiKey="test-key" mapId="map-id" />);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("2 events"));
    expect(mapInstances).toHaveLength(1);
    expect(fetch).toHaveBeenCalledOnce();
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toBe("http://localhost:3000/api/events");

    vi.useFakeTimers();
    act(() => {
      mapInstances[0]?.trigger("idle");
      mapInstances[0]?.trigger("idle");
      mapInstances[0]?.trigger("idle");
    });
    await act(async () => vi.advanceTimersByTimeAsync(299));
    expect(fetch).toHaveBeenCalledOnce();

    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(fetch).toHaveBeenCalledTimes(2);
    const secondRequest = vi.mocked(fetch).mock.calls[1]?.[0];
    const requestUrl = new URL(String(secondRequest));
    expect(Object.fromEntries(requestUrl.searchParams)).toEqual({
      north: "40.1",
      south: "39.8",
      east: "-74.9",
      west: "-75.3",
    });
    vi.useRealTimers();
    await waitFor(() => expect(markerConstructor).toHaveBeenCalledTimes(4));
    expect(markerInstances.slice(0, 2).every((marker) => marker.map === null)).toBe(true);
  });
});
