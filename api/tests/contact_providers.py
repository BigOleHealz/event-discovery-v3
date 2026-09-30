"""Hand-written Google People HTTP replays; no live provider calls."""

from urllib.parse import parse_qs

import httpx

from app.contacts import CONTACT_SCOPE, GoogleContacts


class PeopleReplay:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.scope = CONTACT_SCOPE
        self.email = "person2@example.com"
        self.fail_second_page = False

    def request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            assert form["code"] == ["contacts-code"]
            assert form["code_verifier"]
            return httpx.Response(200, json={"access_token": "fixture-access", "scope": self.scope})
        assert str(request.url).startswith("https://people.example.test/connections?")
        assert request.headers["Authorization"] == "Bearer fixture-access"
        assert request.url.params["personFields"] == "names,emailAddresses,phoneNumbers"
        if request.url.params.get("pageToken") == "page-two":
            return httpx.Response(
                500 if self.fail_second_page else 200,
                json={
                    "connections": [
                        {
                            "names": [{"displayName": "Unmatched Friend"}],
                            "phoneNumbers": [{"canonicalForm": "+14155552671"}],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "connections": [
                    {
                        "names": [{"displayName": "Registered Friend"}],
                        "emailAddresses": [{"value": self.email}],
                    }
                ],
                "nextPageToken": "page-two",
            },
        )

    def provider(self) -> GoogleContacts:
        return GoogleContacts(httpx.MockTransport(self.request))
