import { importLibrary, setOptions } from "@googlemaps/js-api-loader";
import type { MarkerClusterer as MarkerClustererInstance } from "@googlemaps/markerclusterer";
import { useCallback, useEffect, useRef, useState } from "react";

import { EventDetailPanel } from "./EventDetailPanel";
import type { EventFeature, EventMapFeature, EventViewport } from "./events";
import { fetchEvents, isAggregatedGridCell } from "./events";

const PHILADELPHIA_CENTER: google.maps.LatLngLiteral = { lat: 39.9526, lng: -75.1652 };
const VIEWPORT_FETCH_DEBOUNCE_MS = 300;

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

export function EventMap({ apiBaseUrl, apiKey, mapId }: EventMapProps) {
  const mapElement = useRef<HTMLDivElement>(null);
  const [eventCount, setEventCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventFeature | null>(null);
  const closeDetails = useCallback(() => setSelectedEvent(null), []);

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

    async function initializeMap(): Promise<void> {
      configureLoader(apiKey);
      const [events, mapsLibrary, markerLibrary, { MarkerClusterer }] = await Promise.all([
        fetchEvents(apiBaseUrl, requestController.signal),
        importLibrary("maps") as Promise<google.maps.MapsLibrary>,
        importLibrary("marker") as Promise<google.maps.MarkerLibrary>,
        import("@googlemaps/markerclusterer"),
      ]);

      if (cancelled || mapElement.current === null) {
        return;
      }

      const map = new mapsLibrary.Map(mapElement.current, {
        center: PHILADELPHIA_CENTER,
        zoom: 12,
        mapId,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
      });

      function renderEvents(nextEvents: EventMapFeature[]): void {
        clearMarkers();
        const eventMarkers: google.maps.marker.AdvancedMarkerElement[] = [];
        for (const event of nextEvents) {
          const [longitude, latitude] = event.geometry.coordinates;
          const isGridCell = isAggregatedGridCell(event);
          const marker = new markerLibrary.AdvancedMarkerElement({
            ...(isGridCell ? { map } : {}),
            position: { lat: latitude, lng: longitude },
            title: isGridCell
              ? `${event.properties.count} events`
              : event.properties.title,
          });
          marker.dataset[isGridCell ? "eventCell" : "eventMarker"] = event.id;
          marker.append(
            new markerLibrary.PinElement({
              background: isGridCell ? "#59636e" : "#d45d3f",
              borderColor: isGridCell ? "#303841" : "#723524",
              glyphColor: "#fffaf0",
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
        if (eventMarkers.length > 0) {
          markerClusterer = new MarkerClusterer({ map, markers: eventMarkers });
        }
        setEventCount(
          nextEvents.reduce(
            (count, event) => count + (isAggregatedGridCell(event) ? event.properties.count : 1),
            0,
          ),
        );
      }

      async function refetchViewport(): Promise<void> {
        const bounds = map.getBounds();
        if (bounds === undefined) {
          return;
        }
        const northEast = bounds.getNorthEast();
        const southWest = bounds.getSouthWest();
        const viewport: EventViewport = {
          north: northEast.lat(),
          south: southWest.lat(),
          east: northEast.lng(),
          west: southWest.lng(),
          zoom: Math.floor(map.getZoom() ?? 13),
        };
        requestController.abort();
        requestController = new AbortController();
        try {
          const nextEvents = await fetchEvents(
            apiBaseUrl,
            requestController.signal,
            viewport,
          );
          if (!cancelled) {
            renderEvents(nextEvents);
            setError(null);
          }
        } catch (reason: unknown) {
          if (
            !cancelled &&
            !(reason instanceof DOMException && reason.name === "AbortError")
          ) {
            setError(reason instanceof Error ? reason.message : "Unable to refresh the map");
          }
        }
      }

      renderEvents(events);
      idleListener = map.addListener("idle", () => {
        if (viewportTimer !== null) {
          clearTimeout(viewportTimer);
        }
        viewportTimer = setTimeout(() => {
          viewportTimer = null;
          void refetchViewport();
        }, VIEWPORT_FETCH_DEBOUNCE_MS);
      });
    }

    void initializeMap().catch((reason: unknown) => {
      if (!cancelled && !(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof Error ? reason.message : "Unable to load the map");
      }
    });

    return () => {
      cancelled = true;
      requestController.abort();
      idleListener?.remove();
      if (viewportTimer !== null) {
        clearTimeout(viewportTimer);
      }
      clearMarkers();
    };
  }, [apiBaseUrl, apiKey, mapId]);

  return (
    <section className="map-stage" aria-label="Philadelphia event map">
      <div ref={mapElement} className="map-canvas" data-testid="event-map" />
      <div className="map-status" role="status">
        {error ?? (eventCount === null ? "Loading Philadelphia events…" : `${eventCount} events`)}
      </div>
      <EventDetailPanel event={selectedEvent} onClose={closeDetails} />
    </section>
  );
}
