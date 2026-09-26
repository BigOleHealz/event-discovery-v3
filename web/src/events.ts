export interface PointGeometry {
  type: "Point";
  coordinates: [number, number];
}

export interface VenueProperties {
  id: string | null;
  name: string | null;
  formatted_address: string | null;
  city: string | null;
}

export interface RegistrationLink {
  source: string;
  url: string;
}

export interface EventProperties {
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string | null;
  timezone: string;
  primary_category: string | null;
  venue: VenueProperties;
  registration_links: RegistrationLink[];
}

export interface EventFeature {
  type: "Feature";
  id: string;
  geometry: PointGeometry;
  properties: EventProperties;
}

export interface EventFeatureCollection {
  type: "FeatureCollection";
  features: EventFeature[];
}

export interface GridCellProperties {
  count: number;
  top_categories: string[];
}

export interface GridCellFeature {
  type: "Feature";
  id: string;
  geometry: PointGeometry;
  properties: GridCellProperties;
}

export type EventMapFeature = EventFeature | GridCellFeature;

export interface EventMapFeatureCollection {
  type: "FeatureCollection";
  features: EventMapFeature[];
}

export interface EventViewport {
  north: number;
  south: number;
  east: number;
  west: number;
  zoom: number;
}

export interface EventFilters {
  startsAfter: string | null;
  startsBefore: string | null;
  timeOfDayStart: string;
  timeOfDayEnd: string;
  categories: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isPointGeometry(value: unknown): value is PointGeometry {
  if (!isRecord(value) || value.type !== "Point" || !Array.isArray(value.coordinates)) {
    return false;
  }
  return (
    value.coordinates.length === 2 &&
    value.coordinates.every((coordinate) => typeof coordinate === "number")
  );
}

function isVenueProperties(value: unknown): value is VenueProperties {
  return (
    isRecord(value) &&
    isNullableString(value.id) &&
    isNullableString(value.name) &&
    isNullableString(value.formatted_address) &&
    isNullableString(value.city)
  );
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function isRegistrationLink(value: unknown): value is RegistrationLink {
  return isRecord(value) && typeof value.source === "string" && isHttpUrl(value.url);
}

function isEventProperties(value: unknown): value is EventProperties {
  return (
    isRecord(value) &&
    typeof value.title === "string" &&
    isNullableString(value.description) &&
    typeof value.starts_at === "string" &&
    isNullableString(value.ends_at) &&
    typeof value.timezone === "string" &&
    isNullableString(value.primary_category) &&
    isVenueProperties(value.venue) &&
    Array.isArray(value.registration_links) &&
    value.registration_links.every(isRegistrationLink)
  );
}

function isEventFeature(value: unknown): value is EventFeature {
  return (
    isRecord(value) &&
    value.type === "Feature" &&
    typeof value.id === "string" &&
    isPointGeometry(value.geometry) &&
    isEventProperties(value.properties)
  );
}

function isGridCellFeature(value: unknown): value is GridCellFeature {
  if (
    !isRecord(value) ||
    value.type !== "Feature" ||
    typeof value.id !== "string" ||
    !isPointGeometry(value.geometry) ||
    !isRecord(value.properties)
  ) {
    return false;
  }
  return (
    Number.isInteger(value.properties.count) &&
    (value.properties.count as number) > 0 &&
    Array.isArray(value.properties.top_categories) &&
    value.properties.top_categories.every((category) => typeof category === "string")
  );
}

export function isAggregatedGridCell(feature: EventMapFeature): feature is GridCellFeature {
  return "count" in feature.properties;
}

function isEventMapFeatureCollection(value: unknown): value is EventMapFeatureCollection {
  return (
    isRecord(value) &&
    value.type === "FeatureCollection" &&
    Array.isArray(value.features) &&
    value.features.every((feature) => isEventFeature(feature) || isGridCellFeature(feature))
  );
}

export async function fetchEvents(
  apiBaseUrl: string,
  signal: AbortSignal,
  viewport?: EventViewport,
  filters?: EventFilters,
): Promise<EventResult> {
  const endpoint = `${apiBaseUrl.replace(/\/$/, "")}/api/events`;
  const url = new URL(endpoint, window.location.href);
  if (viewport !== undefined) {
    url.searchParams.set("north", String(viewport.north));
    url.searchParams.set("south", String(viewport.south));
    url.searchParams.set("east", String(viewport.east));
    url.searchParams.set("west", String(viewport.west));
    url.searchParams.set("zoom", String(viewport.zoom));
  }
  if (filters !== undefined) {
    if (filters.startsAfter !== null) {
      url.searchParams.set("starts_after", filters.startsAfter);
    }
    if (filters.startsBefore !== null) {
      url.searchParams.set("starts_before", filters.startsBefore);
    }
    if (filters.timeOfDayStart !== "") {
      url.searchParams.set("time_of_day_start", filters.timeOfDayStart);
    }
    if (filters.timeOfDayEnd !== "") {
      url.searchParams.set("time_of_day_end", filters.timeOfDayEnd);
    }
    if (filters.categories.length > 0) {
      url.searchParams.set("categories", filters.categories.join(","));
    }
  }
  let response: Response;
  let payload: unknown;
  try {
    // Public event data never carries cookies or authorization into storage.
    response = await fetch(url, { signal, credentials: "omit", cache: "no-store" });
    if (response.ok) payload = await response.json();
  } catch (reason: unknown) {
    signal.throwIfAborted();
    // Malformed JSON is a server error, not evidence of lost connectivity.
    if (reason instanceof SyntaxError) throw reason;
    const cached = await readCachedEvents(url);
    signal.throwIfAborted();
    if (cached !== null) {
      return { features: cached.features, stale: true };
    }
    throw new Error("Offline or unavailable — no saved events for this viewport and filters.", {
      cause: reason,
    });
  }
  if (!response.ok) {
    throw new Error(`Event request failed with status ${response.status}`);
  }

  if (!isEventMapFeatureCollection(payload)) {
    throw new Error("Event response is not a GeoJSON FeatureCollection");
  }
  signal.throwIfAborted();
  try {
    const cache = await caches.open(EVENT_CACHE_NAME);
    // Persist validated public JSON only, not response headers or credentials.
    await cache.put(url.href, new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
    }));
    const keys = await cache.keys();
    for (const key of keys.slice(0, Math.max(0, keys.length - 50))) {
      await cache.delete(key);
    }
  } catch {
    // Quota, private browsing, or unavailable storage must not break live results.
  }
  signal.throwIfAborted();
  return { features: payload.features, stale: false };
}

// Increment when the public event schema changes. The shell has its own Workbox cache.
export const EVENT_CACHE_NAME = "event-discovery-events-v1";

export interface EventResult {
  features: EventMapFeature[];
  stale: boolean;
}

async function readCachedEvents(url: URL): Promise<EventMapFeatureCollection | null> {
  try {
    const cache = await caches.open(EVENT_CACHE_NAME);
    const response = await cache.match(url.href);
    if (response === undefined) return null;
    const payload: unknown = await response.json();
    return isEventMapFeatureCollection(payload) ? payload : null;
  } catch {
    return null;
  }
}

function viewportKey(apiBaseUrl: string): string {
  return `event-discovery-viewport-v1:${new URL(apiBaseUrl, window.location.href).href}`;
}

export function savedViewport(apiBaseUrl: string): EventViewport | undefined {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(viewportKey(apiBaseUrl)) ?? "null");
    if (isRecord(value) && ["north", "south", "east", "west", "zoom"].every(
      (key) => typeof value[key] === "number" && Number.isFinite(value[key]),
    )) {
      return value as unknown as EventViewport;
    }
  } catch {
    // Storage may be unavailable or contain an old/invalid value.
  }
  return undefined;
}

export function rememberViewport(apiBaseUrl: string, viewport: EventViewport): void {
  try {
    localStorage.setItem(viewportKey(apiBaseUrl), JSON.stringify(viewport));
  } catch {
    // Remembering bounds is best effort; no event data is stored here.
  }
}
