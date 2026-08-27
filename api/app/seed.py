from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import Engine, text

from app.database import create_database_engine

SEED_NAMESPACE = UUID("86eec8dc-e6ce-4ad8-a510-298e4f16af18")
PHILADELPHIA_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class VenueSeed:
    slug: str
    name: str
    address: str
    latitude: float
    longitude: float

    @property
    def id(self) -> UUID:
        return uuid5(SEED_NAMESPACE, f"venue:{self.slug}")


@dataclass(frozen=True)
class EventSeed:
    slug: str
    title: str
    description: str
    starts_at: datetime
    ends_at: datetime
    venue_slug: str
    category: str

    @property
    def id(self) -> UUID:
        return uuid5(SEED_NAMESPACE, f"event:{self.slug}")


VENUES = (
    VenueSeed(
        "academy-of-music",
        "Academy of Music",
        "240 S Broad St, Philadelphia, PA 19102",
        39.9493,
        -75.1656,
    ),
    VenueSeed(
        "franklin-institute",
        "The Franklin Institute",
        "222 N 20th St, Philadelphia, PA 19103",
        39.9582,
        -75.1731,
    ),
    VenueSeed(
        "museum-of-art",
        "Philadelphia Museum of Art",
        "2600 Benjamin Franklin Pkwy, Philadelphia, PA 19130",
        39.9656,
        -75.1809,
    ),
    VenueSeed(
        "world-cafe-live",
        "World Cafe Live",
        "3025 Walnut St, Philadelphia, PA 19104",
        39.9522,
        -75.1852,
    ),
    VenueSeed(
        "reading-terminal",
        "Reading Terminal Market",
        "1136 Arch St, Philadelphia, PA 19107",
        39.9533,
        -75.1593,
    ),
    VenueSeed(
        "penns-landing",
        "Penn's Landing",
        "101 S Christopher Columbus Blvd, Philadelphia, PA 19106",
        39.9468,
        -75.1401,
    ),
    VenueSeed(
        "fillmore",
        "The Fillmore Philadelphia",
        "29 E Allen St, Philadelphia, PA 19123",
        39.9656,
        -75.1358,
    ),
    VenueSeed(
        "kimmel-center",
        "Kimmel Center",
        "300 S Broad St, Philadelphia, PA 19102",
        39.9468,
        -75.1654,
    ),
    VenueSeed(
        "morris-arboretum",
        "Morris Arboretum & Gardens",
        "100 E Northwestern Ave, Philadelphia, PA 19118",
        40.0874,
        -75.2209,
    ),
    VenueSeed(
        "eastern-state",
        "Eastern State Penitentiary",
        "2027 Fairmount Ave, Philadelphia, PA 19130",
        39.9680,
        -75.1727,
    ),
    VenueSeed("bok", "Bok", "800 Mifflin St, Philadelphia, PA 19148", 39.9260, -75.1612),
    VenueSeed(
        "cherry-street-pier",
        "Cherry Street Pier",
        "121 N Christopher Columbus Blvd, Philadelphia, PA 19106",
        39.9523,
        -75.1398,
    ),
)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


EVENTS = (
    EventSeed(
        "parkway-jazz-night",
        "Parkway Jazz Night",
        "An evening of Philadelphia jazz ensembles.",
        at("2026-09-04T19:00:00-04:00"),
        at("2026-09-04T21:30:00-04:00"),
        "museum-of-art",
        "music",
    ),
    EventSeed(
        "science-after-hours",
        "Science After Hours: City Lights",
        "Hands-on exhibits and city-science demonstrations for adults.",
        at("2026-09-05T18:30:00-04:00"),
        at("2026-09-05T22:00:00-04:00"),
        "franklin-institute",
        "science",
    ),
    EventSeed(
        "philly-songwriters-circle",
        "Philadelphia Songwriters Circle",
        "Local songwriters trade original songs and stories.",
        at("2026-09-06T20:00:00-04:00"),
        at("2026-09-06T22:00:00-04:00"),
        "world-cafe-live",
        "music",
    ),
    EventSeed(
        "market-tasting-walk",
        "Reading Terminal Tasting Walk",
        "A guided tasting through independent market merchants.",
        at("2026-09-08T11:00:00-04:00"),
        at("2026-09-08T13:00:00-04:00"),
        "reading-terminal",
        "food",
    ),
    EventSeed(
        "riverfront-film-night",
        "Riverfront Film Night",
        "A family-friendly outdoor film beside the Delaware River.",
        at("2026-09-10T19:30:00-04:00"),
        at("2026-09-10T22:00:00-04:00"),
        "penns-landing",
        "film",
    ),
    EventSeed(
        "broad-street-chamber",
        "Broad Street Chamber Concert",
        "A contemporary chamber program by Philadelphia musicians.",
        at("2026-09-12T19:30:00-04:00"),
        at("2026-09-12T21:30:00-04:00"),
        "kimmel-center",
        "music",
    ),
    EventSeed(
        "fishtown-indie-showcase",
        "Fishtown Indie Showcase",
        "Four emerging regional bands share one stage.",
        at("2026-09-13T20:00:00-04:00"),
        at("2026-09-13T23:30:00-04:00"),
        "fillmore",
        "music",
    ),
    EventSeed(
        "arboretum-bird-walk",
        "Early Autumn Bird Walk",
        "A naturalist-led walk for beginner and experienced birders.",
        at("2026-09-15T08:00:00-04:00"),
        at("2026-09-15T10:00:00-04:00"),
        "morris-arboretum",
        "outdoors",
    ),
    EventSeed(
        "cellblock-history-talk",
        "Cellblock Stories: Philadelphia History",
        "Historians discuss the people and ideas connected to the landmark.",
        at("2026-09-17T18:00:00-04:00"),
        at("2026-09-17T19:30:00-04:00"),
        "eastern-state",
        "history",
    ),
    EventSeed(
        "bok-rooftop-makers",
        "Rooftop Makers Market",
        "Philadelphia artists, printmakers, and food vendors gather at sunset.",
        at("2026-09-19T16:00:00-04:00"),
        at("2026-09-19T20:00:00-04:00"),
        "bok",
        "market",
    ),
    EventSeed(
        "pier-print-fair",
        "Philadelphia Print Fair",
        "Independent presses and illustrators exhibit new work.",
        at("2026-09-20T11:00:00-04:00"),
        at("2026-09-20T17:00:00-04:00"),
        "cherry-street-pier",
        "art",
    ),
    EventSeed(
        "academy-dance-premiere",
        "Philadelphia Dance Premiere",
        "A mixed program of new contemporary dance works.",
        at("2026-09-22T19:00:00-04:00"),
        at("2026-09-22T21:00:00-04:00"),
        "academy-of-music",
        "dance",
    ),
    EventSeed(
        "museum-sketching",
        "Friday Night Gallery Sketching",
        "Drop-in guided sketching in the museum galleries.",
        at("2026-09-25T17:30:00-04:00"),
        at("2026-09-25T19:30:00-04:00"),
        "museum-of-art",
        "art",
    ),
    EventSeed(
        "market-chef-demo",
        "Market Chef Demonstration",
        "A seasonal cooking demonstration with local ingredients.",
        at("2026-09-26T12:00:00-04:00"),
        at("2026-09-26T13:30:00-04:00"),
        "reading-terminal",
        "food",
    ),
    EventSeed(
        "riverfront-lantern-walk",
        "Delaware River Lantern Walk",
        "An evening community walk with handmade lanterns.",
        at("2026-09-27T18:30:00-04:00"),
        at("2026-09-27T20:30:00-04:00"),
        "penns-landing",
        "community",
    ),
    EventSeed(
        "fishtown-comedy-night",
        "Fishtown Comedy Night",
        "A stand-up bill featuring comics from across the region.",
        at("2026-10-01T20:00:00-04:00"),
        at("2026-10-01T22:00:00-04:00"),
        "fillmore",
        "comedy",
    ),
    EventSeed(
        "planetarium-live-score",
        "Planetarium Live Score",
        "An original electronic score accompanies a night-sky program.",
        at("2026-10-03T19:00:00-04:00"),
        at("2026-10-03T21:00:00-04:00"),
        "franklin-institute",
        "science",
    ),
    EventSeed(
        "arboretum-fall-photo",
        "Fall Garden Photography Walk",
        "A photographer-led golden-hour walk through the gardens.",
        at("2026-10-04T16:30:00-04:00"),
        at("2026-10-04T18:30:00-04:00"),
        "morris-arboretum",
        "outdoors",
    ),
    EventSeed(
        "pier-ceramics-workshop",
        "Clay on the Pier Workshop",
        "A beginner-friendly hand-building workshop with local ceramicists.",
        at("2026-10-08T18:00:00-04:00"),
        at("2026-10-08T20:30:00-04:00"),
        "cherry-street-pier",
        "workshop",
    ),
    EventSeed(
        "philly-folk-finale",
        "Philadelphia Folk Weekend Finale",
        "A closing-night bill of acoustic artists and collaborative sets.",
        at("2026-10-11T19:00:00-04:00"),
        at("2026-10-11T22:00:00-04:00"),
        "world-cafe-live",
        "music",
    ),
)


def seed_database(engine: Engine) -> None:
    venues_by_slug = {venue.slug: venue for venue in VENUES}

    with engine.begin() as connection:
        for venue in VENUES:
            connection.execute(
                text(
                    """
                    INSERT INTO venue (
                        id, name, formatted_address, location, city, region, country
                    )
                    VALUES (
                        :id, :name, :address,
                        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                        'Philadelphia', 'PA', 'US'
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        formatted_address = EXCLUDED.formatted_address,
                        location = EXCLUDED.location
                    """
                ),
                {
                    "id": venue.id,
                    "name": venue.name,
                    "address": venue.address,
                    "longitude": venue.longitude,
                    "latitude": venue.latitude,
                },
            )

        for event in EVENTS:
            venue = venues_by_slug[event.venue_slug]
            connection.execute(
                text(
                    """
                    INSERT INTO canonical_event (
                        id, title, description, starts_at, ends_at, timezone,
                        venue_id, location, primary_category
                    )
                    VALUES (
                        :id, :title, :description, :starts_at, :ends_at, :timezone,
                        :venue_id,
                        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                        :category
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        starts_at = EXCLUDED.starts_at,
                        ends_at = EXCLUDED.ends_at,
                        timezone = EXCLUDED.timezone,
                        venue_id = EXCLUDED.venue_id,
                        location = EXCLUDED.location,
                        primary_category = EXCLUDED.primary_category,
                        updated_at = now()
                    """
                ),
                {
                    "id": event.id,
                    "title": event.title,
                    "description": event.description,
                    "starts_at": event.starts_at,
                    "ends_at": event.ends_at,
                    "timezone": PHILADELPHIA_TIMEZONE,
                    "venue_id": venue.id,
                    "longitude": venue.longitude,
                    "latitude": venue.latitude,
                    "category": event.category,
                },
            )


def main() -> None:
    engine = create_database_engine()
    try:
        seed_database(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
