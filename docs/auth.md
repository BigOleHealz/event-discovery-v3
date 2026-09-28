# Google sign-in (6a)

Create a Google OAuth **Web application** client. Set `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, and a random `SESSION_SIGNING_SECRET` of at least 32 bytes.
All API replicas must use the same secret. Keep secrets in the environment.

Register `GOOGLE_OAUTH_REDIRECT_URI` exactly in Google Cloud. The local example is
`http://127.0.0.1:8000/api/auth/google/callback`; `PUBLIC_WEB_BASE_URL` is
`http://127.0.0.1:3000`. Use the same hostname for both services (do not mix localhost
and 127.0.0.1). Production uses HTTPS, `SESSION_COOKIE_SECURE=true`, and a same-site
API host or `/api` reverse proxy. Cross-site frontend/API deployments are not supported
by the SameSite=Lax cookies. CORS origins must explicitly include the web origin.
`PUBLIC_WEB_BASE_URL` must be the web origin, without a path.

The three Google endpoint URLs are configurable through the environment. Their defaults
are listed in `.env.example`. Missing credentials disable sign-in with HTTP 503 while
public browsing remains available. Never put the client secret in a `VITE_` variable.

`GET /api/auth/google/start` issues a ten-minute signed, HttpOnly browser binding and
redirects to Google with state, nonce, and PKCE. Google returns to
`GET /api/auth/google/callback`, which exchanges the code, verifies Google's RSA signature,
audience, issuer, nonce, verified email and timestamps, persists the user by Google subject,
and redirects to the web app. `POST /api/auth/google` also accepts `{code, state}` with the
same browser binding and a trusted Origin header. No Google tokens are persisted.

`GET /api/me` returns only the session's own user. Cookie-backed mutations require the
exact web Origin. Use `current_user` and `require_browser_origin` for future social routes.
Auth responses are `no-store`; the service worker never serves an app shell for API paths.

Sessions use the `SessionStore` issue/validate/revoke interface. The signed cookie contains
only user ID and timestamps, expires after 15 minutes, and can be renewed with
`POST /api/auth/refresh` while valid, up to eight hours from sign-in. These durations are
configurable. The UI refreshes every 30 seconds and on return to the tab; a suspended tab
with an expired cookie requires sign-in again. `POST /api/auth/logout` deletes the cookie.
There is no in-memory, Redis, or database session store. As accepted in the spec, logout
cannot revoke a copied signed cookie; that copy remains valid until expiry and can refresh
until the original eight-hour deadline. Rotating the signing secret invalidates all cookies.

Google subjects are stable identity keys. A different subject with an existing email is
rejected, never silently linked. Shadow-account claiming/merging is the separate 6d.1 task.
The existing initial migration already supplies `app_user`, so 6a needs no migration.

Tests replay a local RSA-signed Google identity and JWKS through an HTTP fixture, exercising
the real token verifier and real Postgres persistence without contacting Google. Browser
coverage uses the same provider behavior against a disposable Postgres-backed API.

Run API checks from `api/`: `pytest`, `ruff check .`, and `mypy`. Run the isolated
browser flow from `api/` with `python tests/run_auth_browser.py` after installing
`requirements-dev.txt`, the web npm dependencies, and Playwright Chromium. The runner
uses ephemeral ports, terminates its servers, and removes its disposable database.
