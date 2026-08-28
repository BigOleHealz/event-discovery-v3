import { useEffect, useRef } from "react";

import type { EventFeature, RegistrationLink } from "./events";

const DATE_TIME_FORMATTERS = new Map<string, Intl.DateTimeFormat>();
const SOURCE_NAMES: Readonly<Record<string, string>> = {
  eventbrite: "Eventbrite",
  meetup: "Meetup",
};

interface EventDetailPanelProps {
  event: EventFeature | null;
  onClose: () => void;
}

function dateTimeFormatter(timeZone: string): Intl.DateTimeFormat {
  const cached = DATE_TIME_FORMATTERS.get(timeZone);
  if (cached !== undefined) {
    return cached;
  }
  const formatter = new Intl.DateTimeFormat(undefined, {
    dateStyle: "full",
    timeStyle: "short",
    timeZone,
  });
  DATE_TIME_FORMATTERS.set(timeZone, formatter);
  return formatter;
}

function formatEventTime(event: EventFeature): string {
  const formatter = dateTimeFormatter(event.properties.timezone);
  const startsAt = formatter.format(new Date(event.properties.starts_at));
  if (event.properties.ends_at === null) {
    return startsAt;
  }
  return `${startsAt} – ${formatter.format(new Date(event.properties.ends_at))}`;
}

function sourceName(source: string): string {
  const knownName = SOURCE_NAMES[source.toLowerCase()];
  if (knownName !== undefined) {
    return knownName;
  }
  return source
    .split(/[-_\s]+/)
    .filter((part) => part.length > 0)
    .map((part) => `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`)
    .join(" ");
}

function RegistrationButton({ registration }: { registration: RegistrationLink }) {
  return (
    <a
      className="registration-button"
      href={registration.url}
      target="_blank"
      rel="noreferrer"
    >
      Register on {sourceName(registration.source)}
    </a>
  );
}

export function EventDetailPanel({ event, onClose }: EventDetailPanelProps) {
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (event === null) {
      return;
    }
    panel.current?.focus();
    const handleKeyDown = (keyboardEvent: KeyboardEvent): void => {
      if (keyboardEvent.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [event, onClose]);

  if (event === null) {
    return null;
  }

  const { properties } = event;
  const venueLocation = properties.venue.formatted_address ?? properties.venue.city;

  return (
    <aside
      ref={panel}
      className="event-detail"
      role="dialog"
      aria-modal="false"
      aria-labelledby="event-detail-title"
      tabIndex={-1}
    >
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close details">
        <span aria-hidden="true">×</span>
      </button>
      <div className="detail-scroll">
        <p className="eyebrow">{properties.primary_category ?? "Philadelphia event"}</p>
        <h2 id="event-detail-title">{properties.title}</h2>
        <time dateTime={properties.starts_at}>{formatEventTime(event)}</time>

        {properties.venue.name === null && venueLocation === null ? null : (
          <section className="detail-section" aria-labelledby="event-detail-venue">
            <h3 id="event-detail-venue">Where</h3>
            {properties.venue.name === null ? null : <p>{properties.venue.name}</p>}
            {venueLocation === null ? null : <p className="detail-muted">{venueLocation}</p>}
          </section>
        )}

        {properties.description === null ? null : (
          <section className="detail-section" aria-labelledby="event-detail-about">
            <h3 id="event-detail-about">About</h3>
            <p>{properties.description}</p>
          </section>
        )}
      </div>

      <footer className="detail-actions">
        {properties.registration_links.length === 0 ? (
          <p className="detail-muted">Registration information is not available.</p>
        ) : (
          properties.registration_links.map((registration) => (
            <RegistrationButton
              key={`${registration.source}:${registration.url}`}
              registration={registration}
            />
          ))
        )}
      </footer>
    </aside>
  );
}
