import { importLibrary, setOptions } from "@googlemaps/js-api-loader";
import { useCallback, useEffect, useRef, useState } from "react";

import { EventDetailPanel } from "./EventDetailPanel";
import type { EventFeature } from "./events";
import { fetchEvents } from "./events";

const PHILADELPHIA_CENTER: google.maps.LatLngLiteral = { lat: 39.9526, lng: -75.1652 };

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
    const controller = new AbortController();
    const markers: google.maps.marker.AdvancedMarkerElement[] = [];
    const markerListeners: google.maps.MapsEventListener[] = [];
    let cancelled = false;

    async function initializeMap(): Promise<void> {
      configureLoader(apiKey);
      const [events, mapsLibrary, markerLibrary] = await Promise.all([
        fetchEvents(apiBaseUrl, controller.signal),
        importLibrary("maps") as Promise<google.maps.MapsLibrary>,
        importLibrary("marker") as Promise<google.maps.MarkerLibrary>,
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

      for (const event of events) {
        const [longitude, latitude] = event.geometry.coordinates;
        const marker = new markerLibrary.AdvancedMarkerElement({
          map,
          position: { lat: latitude, lng: longitude },
          title: event.properties.title,
        });
        marker.dataset.eventMarker = event.id;
        marker.append(
          new markerLibrary.PinElement({
            background: "#d45d3f",
            borderColor: "#723524",
            glyphColor: "#fffaf0",
            scale: 0.92,
          }),
        );
        markerListeners.push(marker.addListener("click", () => setSelectedEvent(event)));
        markers.push(marker);
      }

      setEventCount(events.length);
    }

    void initializeMap().catch((reason: unknown) => {
      if (!cancelled && !(reason instanceof DOMException && reason.name === "AbortError")) {
        setError(reason instanceof Error ? reason.message : "Unable to load the map");
      }
    });

    return () => {
      cancelled = true;
      controller.abort();
      for (const listener of markerListeners) {
        listener.remove();
      }
      for (const marker of markers) {
        marker.map = null;
      }
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
