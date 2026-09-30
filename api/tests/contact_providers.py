"""Hand-written Google People/Twilio HTTP replays; no live provider calls."""

from urllib.parse import parse_qs

import httpx

from app.contacts import CONTACT_SCOPE, GoogleContacts
from app.sms import TwilioSMS


class TwilioReplay:
    def __init__(self) -> None:
        self.calls: list[dict[str, list[str]]] = []
        self.status = 201
        self.timeout = False

    def request(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://twilio.example.test/Messages.json"
        assert request.method == "POST"
        assert request.headers["Authorization"].startswith("Basic ")
        form = parse_qs(request.content.decode())
        assert form["From"] == ["+15005550006"]
        self.calls.append(form)
        if self.timeout:
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(
            self.status,
            json={
                "sid": "SM" + f"{len(self.calls):032d}",
                "status": "queued",
                "error_code": None,
            },
        )

    def provider(self) -> TwilioSMS:
        return TwilioSMS(httpx.MockTransport(self.request))


class PeopleReplay:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.scope = CONTACT_SCOPE
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
                            "names": [{"displayName": "SMS Friend"}],
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
                        "emailAddresses": [{"value": "person2@example.com"}],
                    }
                ],
                "nextPageToken": "page-two",
            },
        )

    def provider(self) -> GoogleContacts:
        return GoogleContacts(httpx.MockTransport(self.request))
