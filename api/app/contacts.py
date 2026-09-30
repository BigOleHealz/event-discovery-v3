"""Private contacts, vCard imports, and explicit Google Contacts consent."""

import os
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import Connection, text

from app.auth import Clock, Config, CurrentUser, Database, require_browser_origin
from app.contact_import import ContactInput, parse_vcard
from app.oauth import AuthConfig, begin_oauth, clear_attempt, validate_attempt

router = APIRouter(prefix="/api/contacts", tags=["contacts"])
CONTACT_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"


class Contact(ContactInput):
    id: UUID
    matched_user_id: UUID | None
    source: str
    imported_at: datetime


class ContactImport(BaseModel):
    contacts: list[ContactInput] = Field(default_factory=list, max_length=2000)
    vcard: str | None = Field(default=None, max_length=1_000_000)

    @model_validator(mode="after")
    def one_format(self) -> "ContactImport":
        if bool(self.contacts) == bool(self.vcard):
            raise ValueError("Supply contacts or a vCard file")
        return self


def save_contacts(
    connection: Connection,
    owner: UUID,
    contacts: list[ContactInput],
    source: Literal["manual", "device", "google_contacts"],
    now: datetime,
) -> int:
    # Serialize imports per owner, including batches containing the same address twice.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('contacts:' || CAST(:id AS text), 0))"),
        {"id": owner},
    )
    ids: set[UUID] = set()
    for contact in contacts:
        ids.add(
            connection.execute(
                text("""
            INSERT INTO contact (id, owner_user_id, display_name, phone_e164, email,
                                 source, imported_at)
            VALUES (:id, :owner, :name, :phone, :email, :source, :now)
            ON CONFLICT (owner_user_id, (coalesce(phone_e164, lower(email))))
            DO UPDATE SET display_name=EXCLUDED.display_name,
                email=EXCLUDED.email, source=EXCLUDED.source, imported_at=EXCLUDED.imported_at
            RETURNING id
        """),
                {
                    "id": uuid4(),
                    "owner": owner,
                    "name": contact.display_name,
                    "phone": contact.phone_e164,
                    "email": contact.email,
                    "source": source,
                    "now": now,
                },
            ).scalar_one()
        )
    connection.execute(text("SELECT match_contacts_to_users()"))
    connection.commit()
    return len(ids)


@router.get("")
def list_contacts(
    user: CurrentUser,
    connection: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=255)] = "",
) -> list[Contact]:
    rows = connection.execute(
        text("""
        SELECT id, display_name, phone_e164, email, matched_user_id, source, imported_at
        FROM contact WHERE owner_user_id=:owner
          AND (strpos(lower(coalesce(display_name,'') || ' ' || coalesce(email,'') || ' ' ||
                           coalesce(phone_e164,'')), lower(:q)) > 0)
        ORDER BY lower(coalesce(display_name, email, phone_e164)), id LIMIT :limit OFFSET :offset
    """),
        {"owner": user.id, "q": q, "limit": limit, "offset": offset},
    ).mappings()
    return [Contact.model_validate(dict(row)) for row in rows]


@router.post("/import", dependencies=[Depends(require_browser_origin)])
def import_contacts(
    payload: ContactImport,
    user: CurrentUser,
    connection: Database,
    now: Clock,
) -> dict[str, int]:
    try:
        rows = parse_vcard(payload.vcard) if payload.vcard else payload.contacts
    except ValueError as error:
        raise HTTPException(
            422,
            "Invalid vCard. Use valid emails or phone numbers without extensions. "
            "Numbers without a country code default to +1.",
        ) from error
    return {
        "imported": save_contacts(
            connection,
            user.id,
            rows,
            "device" if payload.vcard else "manual",
            now,
        )
    }


def contacts_config(config: AuthConfig) -> tuple[AuthConfig, str]:
    redirect = os.getenv("GOOGLE_CONTACTS_REDIRECT_URI", "")
    people_url = os.getenv("GOOGLE_PEOPLE_CONNECTIONS_URL", "")
    for value in (redirect, people_url):
        url = urlsplit(value)
        if (
            not url.netloc
            or url.username
            or url.password
            or url.fragment
            or url.query
            or (
                url.scheme != "https"
                and not (
                    not config.secure
                    and url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "localhost"}
                )
            )
        ):
            raise HTTPException(503, "Google Contacts is not configured")
    return replace(config, redirect_uri=redirect), people_url


class GoogleContacts:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def fetch(
        self,
        config: AuthConfig,
        people_url: str,
        code: str,
        verifier: str,
    ) -> list[ContactInput]:
        contacts: list[ContactInput] = []
        try:
            with httpx.Client(transport=self.transport, timeout=15) as client:
                response = client.post(
                    config.token_url,
                    data={
                        "code": code,
                        "client_id": config.client_id,
                        "client_secret": config.client_secret,
                        "redirect_uri": config.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": verifier,
                    },
                )
                response.raise_for_status()
                tokens = response.json()
                if CONTACT_SCOPE not in tokens.get("scope", "").split():
                    raise ValueError("Contacts consent was not granted")
                token = tokens["access_token"]
                params = {"personFields": "names,emailAddresses,phoneNumbers", "pageSize": "1000"}
                # Bound pagination; never follow a provider-supplied URL with a bearer token.
                for _ in range(10):
                    response = client.get(
                        people_url, params=params, headers={"Authorization": f"Bearer {token}"}
                    )
                    response.raise_for_status()
                    data = response.json()
                    for person in data.get("connections", []):
                        names = person.get("names", [])
                        name = names[0].get("displayName") if names else None
                        emails = [entry["value"] for entry in person.get("emailAddresses", [])]
                        phones = [
                            entry.get("canonicalForm", entry.get("value"))
                            for entry in person.get("phoneNumbers", [])
                        ]
                        if not emails and not phones:
                            continue
                        for phone in phones or [None]:
                            contacts.append(
                                ContactInput(
                                    display_name=name,
                                    phone_e164=phone,
                                    email=emails[0] if emails else None,
                                )
                            )
                        for email in emails[1:]:
                            contacts.append(ContactInput(display_name=name, email=email))
                    if len(contacts) > 2000:
                        raise HTTPException(422, "Import at most 2,000 contact addresses at a time")
                    if not data.get("nextPageToken"):
                        return contacts
                    params["pageToken"] = data["nextPageToken"]
                raise ValueError("Too many contact pages")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as error:
            raise HTTPException(502, "Google Contacts import could not be completed") from error


def google_contacts() -> GoogleContacts:
    return GoogleContacts()


@router.get("/google/start")
def google_start(user: CurrentUser, config: Config, now: Clock) -> Response:
    contacts_auth, _ = contacts_config(config)
    response = RedirectResponse(config.web_url, status_code=303)
    response.headers["Location"] = begin_oauth(
        response,
        contacts_auth,
        now,
        scope=CONTACT_SCOPE,
        owner_id=str(user.id),
        purpose="contacts",
    )
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    user: CurrentUser,
    config: Config,
    now: Clock,
    connection: Database,
    provider: Annotated[GoogleContacts, Depends(google_contacts)],
    code: str = "",
    state: str = "",
    error: str | None = None,
) -> Response:
    response = RedirectResponse(config.web_url + "/?contacts_imported=1", status_code=303)
    try:
        contacts_auth, people_url = contacts_config(config)
        attempt = validate_attempt(request, state, contacts_auth, now)
        if (
            attempt.purpose != "contacts"
            or attempt.owner_id != str(user.id)
            or error
            or not code
            or len(code) > 4096
        ):
            raise HTTPException(400, "Invalid contacts consent")
        rows = provider.fetch(contacts_auth, people_url, code, attempt.verifier)
        save_contacts(connection, user.id, rows, "google_contacts", now)
    except HTTPException:
        response = RedirectResponse(config.web_url + "/?contacts_error=1", status_code=303)
    clear_attempt(response, config)
    return response
