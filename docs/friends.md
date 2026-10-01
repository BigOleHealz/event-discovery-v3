# Friends and friends going (Phase 6e)

Sign in, open **Friends**, and send a request to another user's account email. They open
**Friends** and choose **Accept friend request**. Either person can remove the friendship;
pending requests can be cancelled or declined. Duplicate and reverse requests keep one
pending pair and never imply acceptance. Accepting an event invitation or importing a
contact does not make people friends automatically.

Enable **Friends going** on the map. A corner badge marks events attended by accepted
friends without changing the category colour. At low zoom, a neutral map cell receives a
badge when it contains an event a friend is going to. Open an event for friends' names and
avatars. **Refresh friends going**, returning to the page, or the 30-second visible-page
refresh picks up new acceptances. The overlay respects the current viewport and filters.
Graph failures clear the overlay and show an error; public events remain usable.

## Storage and privacy

Migration `20261001_0020` adds `friendship`: one ordered UUID pair, an explicit requester,
creation time, and optional acceptance time. Postgres is authoritative. Only accepted pairs
project to one `FRIENDS_WITH` edge, traversed in either direction. The full graph rebuild
recreates those edges along with invitation provenance and attendance.

For immediate results, the authenticated friends endpoints refresh the viewer's accepted
friendships and those friends' current `ATTENDING` edges before traversing Neo4j, in one
graph transaction. They take the same Postgres advisory lock as the full projection so a
scheduled rebuild cannot overwrite that refresh with an older snapshot. Friendship writes
use that lock too. This is intentionally a simple serialized approach for hobby-scale use.
Event bounds, dates, categories, archival state, and display information come from Postgres.

`GET /api/friends` is scoped to the session user. `POST /api/friends` accepts an account
email; `POST /api/friends/{user_id}/accept` accepts an incoming request; the corresponding
`/remove` route declines, cancels, or removes a pair involving the session user. All writes
require the configured browser Origin. There is no arbitrary user-id parameter for reading
another person's friend list or attendance.

`GET /api/events/friends` requires viewport bounds and accepts the public event filters and
zoom. It returns event IDs with attending friends at individual zoom, or cell IDs with
counts of events with friends at aggregate zoom. `GET /api/events/{event_id}/friends`
returns friends attending that upcoming, unarchived event. Responses expose names and
avatars, never friends' email addresses, phone numbers, or invitation history.

Every friends response is `no-store`. The frontend keeps the overlay separate from public
event data and offline storage, clears it on sign-out/offline/error, and ignores cancelled
requests when the account or viewport changes. An event invite can be accepted without
granting attendance visibility to its creator; friendship requires separate mutual consent.
