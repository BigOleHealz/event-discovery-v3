import { importLibrary, setOptions } from "@googlemaps/js-api-loader";
import type { MarkerClusterer as MarkerClustererInstance } from "@googlemaps/markerclusterer";
import { useCallback, useEffect, useRef, useState } from "react";

import { EventDetailPanel } from "./EventDetailPanel";
import { EventFilterSidebar } from "./EventFilterSidebar";
import { AGGREGATED_CELL_PIN_STYLE, pinStyleForCategory } from "./categoryPinStyle";
import { readEventFilters, replaceEventFilterUrl } from "./eventFilterState";
import type { EventFeature, EventFilters, EventMapFeature } from "./events";
import { fetchEvents, isAggregatedGridCell, rememberViewport, savedViewport } from "./events";

const PHILADELPHIA_CENTER: google.maps.LatLngLiteral = { lat: 39.9526, lng: -75.1652 };
const VIEWPORT_FETCH_DEBOUNCE_MS = 300;
const RECOVERY_RETRY_MS = 10_000;

let configuredApiKey: string | null = null;

function configureLoader(apiKey: string): void {
  if (configuredApiKey === null) {
    setOptions({ key: apiKey, v: "weekly" });
    configuredApiKey = apiKey;
    return;
  }
  if (configuredApiKey !== apiKey) {
    throw new Error("Google Maps loader cannot be reconfigured with a different API key");
  }
}

interface EventMapProps {
  apiBaseUrl: string;
  apiKey: string;
  mapId: string;
}

function categoriesIn(features: EventMapFeature[]): string[] {
  const categories = new Set<string>();
  for (const feature of features) {
    if (isAggregatedGridCell(feature)) {
      for (const category of feature.properties.top_categories) {
        categories.add(category);
      }
    } else if (feature.properties.primary_category !== null) {
      categories.add(feature.properties.primary_category);
    }
  }
  return Array.from(categories).sort();
}

export function EventMap({ apiBaseUrl, apiKey, mapId }: EventMapProps) {
  const mapElement = useRef<HTMLDivElement>(null);
  const [filters, setFilters] = useState<EventFilters>(() => readEventFilters());
  const filtersRef = useRef(filters);
  const refetchViewportRef = useRef<((retryMap?: boolean) => Promise<void>) | null>(null);
  const [availableCategories, setAvailableCategories] = useState<string[]>(filters.categories);
  const [eventCount, setEventCount] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const [mapUnavailable, setMapUnavailable] = useState(false);
  const [features, setFeatures] = useState<EventMapFeature[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventFeature | null>(null);
  const closeDetails = useCallback(() => setSelectedEvent(null), []);
  const changeFilters = useCallback((nextFilters: EventFilters) => {
    setSelectedEvent(null);
    setFilters(nextFilters);
  }, []);

  useEffect(() => {
    let requestController = new AbortController();
    let markers: google.maps.marker.AdvancedMarkerElement[] = [];
    let markerListeners: google.maps.MapsEventListener[] = [];
    let markerClusterer: MarkerClustererInstance | null = null;
    let idleListener: google.maps.MapsEventListener | null = null;
    let viewportTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    function clearMarkers(): void {
      markerClusterer?.clearMarkers(true);
      markerClusterer?.setMap(null);
      markerClusterer = null;
      for (const listener of markerListeners) {
        listener.remove();
      }
      for (const marker of markers) {
        marker.map = null;
      }
      markerListeners = [];
      markers = [];
    }

    let map: google.maps.Map | null = null;
    let markerLibrary: google.maps.MarkerLibrary | null = null;
    let Clusterer: typeof import("@googlemaps/markerclusterer").MarkerClusterer | null = null;
    let lastViewport = savedViewport(apiBaseUrl);
    let initializing = false;
    let renderedRequest: string | null = null;

    function renderEvents(nextEvents: EventMapFeature[]): void {
      clearMarkers();
      setFeatures(nextEvents);
      setSelectedEvent((current) => nextEvents.find(
        (event): event is EventFeature => !isAggregatedGridCell(event) && event.id === current?.id,
      ) ?? null);
      const discoveredCategories = categoriesIn(nextEvents);
      if (discoveredCategories.length > 0) {
        setAvailableCategories((currentCategories) => {
          const nextCategories = new Set([...currentCategories, ...discoveredCategories]);
          return nextCategories.size === currentCategories.length
            ? currentCategories
            : Array.from(nextCategories).sort();
        });
      }
      const eventMarkers: google.maps.marker.AdvancedMarkerElement[] = [];
      for (const event of map && markerLibrary ? nextEvents : []) {
        const [longitude, latitude] = event.geometry.coordinates;
        const isGridCell = isAggregatedGridCell(event);
        const pinStyle = isGridCell
          ? AGGREGATED_CELL_PIN_STYLE
          : pinStyleForCategory(event.properties.primary_category);
        const marker = new markerLibrary!.AdvancedMarkerElement({
          ...(isGridCell ? { map } : {}),
          position: { lat: latitude, lng: longitude },
          title: isGridCell
            ? `${event.properties.count} events`
            : event.properties.title,
        });
        marker.dataset[isGridCell ? "eventCell" : "eventMarker"] = event.id;
        marker.append(
          new markerLibrary!.PinElement({
            ...pinStyle,
            glyph: isGridCell ? String(event.properties.count) : undefined,
            scale: isGridCell ? 1.08 : 0.92,
          }),
        );
        if (!isGridCell) {
          markerListeners.push(marker.addListener("click", () => setSelectedEvent(event)));
          eventMarkers.push(marker);
        }
        markers.push(marker);
      }
      if (eventMarkers.length > 0 && Clusterer !== null) {
        markerClusterer = new Clusterer({ map, markers: eventMarkers });
      }
      setEventCount(
        nextEvents.reduce(
          (count, event) => count + (isAggregatedGridCell(event) ? event.properties.count : 1),
          0,
        ),
      );
    }

    async function refetchViewport(): Promise<void> {
      const bounds = map?.getBounds();
      if (map !== null && bounds === undefined) return;
      if (bounds !== undefined) {
        const northEast = bounds.getNorthEast();
        const southWest = bounds.getSouthWest();
        lastViewport = {
          north: northEast.lat(), south: southWest.lat(),
          east: northEast.lng(), west: southWest.lng(),
          zoom: Math.floor(map?.getZoom() ?? 13),
        };
        rememberViewport(apiBaseUrl, lastViewport);
      }
      requestController.abort();
      requestController = new AbortController();
      const controller = requestController;
      setError(null);
      if (lastViewport === undefined) {
        setError("Offline or unavailable — no saved viewport. Connect to load the map.");
        return;
      }
      const requestKey = JSON.stringify([lastViewport, filtersRef.current]);
      if (requestKey !== renderedRequest) {
        clearMarkers();
        setFeatures([]);
        setSelectedEvent(null);
        setEventCount(null);
      }
      try {
        const result = await fetchEvents(
          apiBaseUrl,
          controller.signal,
          lastViewport,
          filtersRef.current,
        );
        if (!cancelled && !controller.signal.aborted) {
          renderEvents(result.features);
          renderedRequest = requestKey;
          setStale(result.stale);
          setError(null);
        }
      } catch (reason: unknown) {
        if (
          !cancelled && !controller.signal.aborted &&
          !(reason instanceof DOMException && reason.name === "AbortError")
        ) {
          clearMarkers();
          setFeatures([]);
          setSelectedEvent(null);
          setEventCount(null);
          renderedRequest = null;
          setStale(false);
          setError(reason instanceof Error ? reason.message : "Unable to refresh the map");
        }
      }
    }

    refetchViewportRef.current = async (retryMap = false) => {
      // Wait for the initial map bounds; never substitute a saved area for a live map.
      if (initializing && map === null) return;
      if (retryMap && map === null) {
        await initializeMap();
        return;
      }
      await refetchViewport();
    };

    async function initializeMap(): Promise<void> {
      if (initializing || map !== null || cancelled) return;
      if (!navigator.onLine) {
        setMapUnavailable(true);
        await refetchViewport();
        return;
      }
      initializing = true;
      try {
        configureLoader(apiKey);
        const [mapsLibrary, markersLibrary, clustering] = await Promise.all([
          importLibrary("maps") as Promise<google.maps.MapsLibrary>,
          importLibrary("marker") as Promise<google.maps.MarkerLibrary>,
          import("@googlemaps/markerclusterer"),
        ]);
        if (cancelled || mapElement.current === null) return;
        markerLibrary = markersLibrary;
        Clusterer = clustering.MarkerClusterer;
        map = new mapsLibrary.Map(mapElement.current, {
          center: lastViewport === undefined ? PHILADELPHIA_CENTER : {
            lat: (lastViewport.north + lastViewport.south) / 2,
            lng: (lastViewport.east + lastViewport.west) / 2,
          },
          zoom: lastViewport?.zoom ?? 12,
          mapId, mapTypeControl: false, streetViewControl: false, fullscreenControl: true,
        });
        setMapUnavailable(false);
        idleListener = map.addListener("idle", () => {
          // Invalidate in-flight results as soon as the viewport changes, before debounce.
          requestController.abort();
          renderedRequest = null;
          clearMarkers();
          setFeatures([]);
          setSelectedEvent(null);
          setEventCount(null);
          if (viewportTimer !== null) clearTimeout(viewportTimer);
          viewportTimer = setTimeout(() => {
            viewportTimer = null;
            void refetchViewport();
          }, VIEWPORT_FETCH_DEBOUNCE_MS);
        });
        await refetchViewport();
      } catch {
        if (!cancelled) {
          setMapUnavailable(true);
          await refetchViewport();
        }
      } finally {
        initializing = false;
      }
    }

    function reconnect(): void {
      if (map === null) void initializeMap();
      else void refetchViewport();
    }
    function disconnect(): void { void refetchViewport(); }
    window.addEventListener("online", reconnect);
    window.addEventListener("offline", disconnect);
    void initializeMap();

    return () => {
      cancelled = true;
      window.removeEventListener("online", reconnect);
      window.removeEventListener("offline", disconnect);
      requestController.abort();
      idleListener?.remove();
      if (viewportTimer !== null) {
        clearTimeout(viewportTimer);
      }
      clearMarkers();
      refetchViewportRef.current = null;
    };
  }, [apiBaseUrl, apiKey, mapId]);

  useEffect(() => {
    filtersRef.current = filters;
    replaceEventFilterUrl(filters);
    void refetchViewportRef.current?.();
  }, [filters]);

  useEffect(() => {
    if (!stale && error === null) return;
    // Connectivity events are hints: an offline shell can reopen reporting online.
    // Retry only while unavailable, visible, and the browser thinks networking works.
    const timer = window.setInterval(() => {
      if (navigator.onLine && document.visibilityState === "visible") {
        void refetchViewportRef.current?.(true);
      }
    }, RECOVERY_RETRY_MS);
    return () => window.clearInterval(timer);
  }, [stale, error]);

  useEffect(() => {
    function restoreFiltersFromUrl(): void {
      changeFilters(readEventFilters());
    }
    window.addEventListener("popstate", restoreFiltersFromUrl);
    return () => window.removeEventListener("popstate", restoreFiltersFromUrl);
  }, [changeFilters]);

  return (
    <section className="map-stage" aria-label="Philadelphia event map">
      <div ref={mapElement} className="map-canvas" data-testid="event-map" />
      {stale ? <div className="offline-banner">
        <span role="alert">Offline — showing last known events</span>
        <button type="button" onClick={() => void refetchViewportRef.current?.(true)}>
          Refresh events
        </button>
      </div> : null}
      {mapUnavailable ? <section className="offline-events" aria-label="Saved viewport events">
        <h2>Map unavailable</h2>
        <p>Events for the last viewed area. Connect to move the map or search new areas.</p>
        {features.map((event) => isAggregatedGridCell(event)
          ? <p key={event.id}>{event.properties.count} {event.properties.count === 1 ? "event" : "events"} in a map cell — connect and zoom in for details.</p>
          : <button key={event.id} type="button" onClick={() => setSelectedEvent(event)}>
              {event.properties.title}
            </button>)}
      </section> : null}
      <EventFilterSidebar
        availableCategories={availableCategories}
        filters={filters}
        onChange={changeFilters}
      />
      <div className="map-status" role="status">
        {error ?? (eventCount === null
          ? "Loading Philadelphia events…"
          : `${eventCount} ${eventCount === 1 ? "event" : "events"}`)}
        {error !== null ? <button type="button" onClick={() => void refetchViewportRef.current?.(true)}>
          Refresh events
        </button> : null}
      </div>
      <EventDetailPanel event={selectedEvent} onClose={closeDetails} />
    </section>
  );
}
