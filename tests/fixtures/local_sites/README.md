# Recorded local calendars

Captured on 2026-09-27 during Phase 8c onboarding:

- `reads-and-company.html`: https://www.readsandcompany.com/events, rendered by the
  Stagehand container; readiness `#maintable h2`.
- `charm-city-books.html`: https://www.charmcitybooks.com/events, rendered by the
  Stagehand container; readiness `#main-content h4`.
- `philamoca.html`: https://www.philamoca.org/, ordinary HTTP.
- `*-http.html`: the bookstore HTTP shells, which have no event listings. Both captures use their exact `/events` listing URL.

Scripts, styles and SVG paths are removed from rendered captures, matching model-input
sanitization. Event DOM, attributes and surrounding page structure are retained. Tests only
parse these files; they do not navigate embedded links or load assets.

Original rendered/HTTP snapshot SHA-256 hashes before sanitization:

- Reads & Company: `006809ead4634bc87382a6a6f5fdf54e25f58fdd344229953d61b1bb53ab1916`
- Charm City Books: `03d716bff0eed7eb3fbeff6fe2d8877b1994bedd3c7279589ddcc685d569aeae`
- PhilaMOCA: `1bc357976af8285d750a03d5a86559046721ac34073c940bbe3fb5f021cd5bd0`

The `*-plan.json` files are recorded derivation outputs from `gpt-5.4-2026-03-05`. The test transport wraps these
plans in a deterministic Responses envelope, labelled `deterministic-plan-substitute` so
test provenance is unmistakable. Production derives its own plans; these files are never
seeded into the extraction cache. No test needs a model key or source access.

These captures cover the reviewed subset (8, 9 and 55 events respectively), including
out-of-scope entries that must be filtered. See `docs/local-sources.md` for policy evidence,
venue restrictions, identity behavior and limitations.
