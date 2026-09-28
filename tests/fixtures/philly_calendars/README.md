# Recorded Philadelphia calendar excerpts

Recorded 2026-09-28 via ordinary HTTP from:

- https://gridphilly.com/events/ and https://gridphilly.com/events/list/page/2/
- https://www.bartramsgarden.org/calendar/ and https://www.bartramsgarden.org/calendar/list/page/2/
- https://fleisher.org/calendar/ and https://fleisher.org/calendar/list/page/2/

HTML fixtures retain the captured JSON-LD script contents and next-page navigation links.
Other markup, styling and executable scripts were removed to keep fixtures focused. Event
values were not synthesized. These are fixture excerpts, not full-page browser captures.
`plan.json` is a deterministic model substitute; runtime plans are derived by the configured
model and cached in Postgres, not seeded from this file. Tests never fetch these sites.

Access review and extraction exclusions are described in `docs/philly-calendars.md`.
