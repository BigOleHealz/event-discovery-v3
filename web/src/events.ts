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
): Promise<EventMapFeature[]> {
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
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`Event request failed with status ${response.status}`);
  }

  const payload: unknown = await response.json();
  if (!isEventMapFeatureCollection(payload)) {
    throw new Error("Event response is not a GeoJSON FeatureCollection");
  }
  return payload.features;
}
