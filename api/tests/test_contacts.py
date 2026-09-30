from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
import pytest
from contact_providers import PeopleReplay
from fastapi.testclient import TestClient
from ingestion.contacts import match_contacts_to_users
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_auth import CONFIG, NOW
from test_invites import InviteData, invite_data  # noqa: F401

from app.auth import save_google_user
from app.contacts import google_contacts
from app.main import app
from app.oauth import GoogleIdentity

VCARD = """BEGIN:VCARD
VERSION:3.0
FN:Registered Friend
EMAIL:PERSON2@example.com
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Unmatched Friend
TEL;TYPE=CELL:+1 (415) 555-2671
END:VCARD
"""


@pytest.fixture
def contacts_data(invite_data: InviteData, monkeypatch: pytest.MonkeyPatch) -> Iterator[InviteData]:  # noqa: F811
    for key, value in {
        "GOOGLE_CONTACTS_REDIRECT_URI": "https://api.example.test/api/contacts/google/callback",
        "GOOGLE_PEOPLE_CONNECTIONS_URL": "https://people.example.test/connections",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("ACCOUNT_SID", "AUTH_TOKEN", "FROM_NUMBER", "MESSAGES_URL"):
        monkeypatch.delenv("TWILIO_" + key, raising=False)
    try:
        yield invite_data
    finally:
        with invite_data.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM invite WHERE canonical_event_id=:id"), {"id": invite_data.event}
            )
            conn.execute(
                text("DELETE FROM contact WHERE owner_user_id=ANY(:ids)"),
                {"ids": invite_data.users},
            )
            conn.execute(
                text("DELETE FROM ingest.run WHERE airflow_dag_id='match_contacts_to_users'")
            )


def imported(client: TestClient) -> list[dict[str, object]]:
    response = client.post("/api/contacts/import", json={"vcard": VCARD})
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 2}
    return list(client.get("/api/contacts").json())


def test_owner_privacy_normalization_and_idempotent_import(contacts_data: InviteData) -> None:
    data = contacts_data
    with data.client(0) as owner, data.client(1) as other:
        first = imported(owner)
        assert imported(owner) == first
        assert first[0]["email"] == "person2@example.com"
        assert first[0]["matched_user_id"] == str(data.users[2])
        assert first[1]["phone_e164"] == "+14155552671"
        assert other.get("/api/contacts", params={"owner_user_id": str(data.users[0])}).json() == []
        assert owner.get("/api/contacts").headers["cache-control"] == "no-store"
        assert owner.get("/api/contacts?q=Unmatched&limit=1").json() == [first[1]]
        assert owner.get("/api/contacts?offset=1").json() == [first[1]]
        assert (
            other.post(
                "/api/invites",
                json={"canonical_event_id": str(data.event), "contact_ids": [first[1]["id"]]},
            ).status_code
            == 404
        )
        assert (
            owner.post(
                "/api/contacts/import",
                json={"vcard": VCARD},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        with TestClient(app) as anonymous:
            assert anonymous.get("/api/contacts").status_code == 401
            assert anonymous.post("/api/contacts/import", json={"vcard": VCARD}).status_code in (
                401,
                403,
            )
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO contact (id, owner_user_id, phone_e164)
            VALUES (:id, :owner, '+14155552671')
        """),
            {"id": uuid4(), "owner": data.users[0]},
        )


@pytest.mark.parametrize(
    "vcard",
    [
        "garbage",
        VCARD.replace("+1 (415) 555-2671", "12345"),
        VCARD.replace("PERSON2@example.com", "invalid-email"),
    ],
)
def test_invalid_import_is_atomic(contacts_data: InviteData, vcard: str) -> None:
    with contacts_data.client(0) as client:
        assert client.post("/api/contacts/import", json={"vcard": vcard}).status_code == 422
        assert client.get("/api/contacts").json() == []


def test_apple_vcard_phone_formats_reimport_and_match(contacts_data: InviteData) -> None:
    data = contacts_data
    with data.engine.begin() as conn:
        conn.execute(
            text("UPDATE app_user SET phone_e164='+14155552671' WHERE id=:id"),
            {"id": data.users[2]},
        )
    with data.client(0) as client:
        contact_id = None
        for phone in ("(415) 555-2671", "1 (415) 555-2671", "+1 (415) 555-2671"):
            vcard = (
                "BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Apple Inc.//iPhone OS//EN\r\n"
                "N:García;Zoë;;;\r\nFN:Zoë García\r\n"
                f"item1.TEL;type=CELL;type=VOICE;type=pref:{phone}\r\n"
                "item1.X-ABLabel:iPhone\r\nEND:VCARD\r\n"
            )
            response = client.post("/api/contacts/import", json={"vcard": vcard})
            assert response.status_code == 200, response.text
            contacts = client.get("/api/contacts").json()
            assert len(contacts) == 1
            contact = contacts[0]
            assert contact["display_name"] == "Zoë García"
            assert contact["phone_e164"] == "+14155552671"
            assert contact["matched_user_id"] == str(data.users[2])
            if contact_id is not None:
                assert contact["id"] == contact_id
            contact_id = contact["id"]


def test_matched_contact_and_email_share_one_invite(contacts_data: InviteData) -> None:
    data = contacts_data
    with data.client(0) as client:
        contacts = imported(client)
        payload = {
            "canonical_event_id": str(data.event),
            "contact_ids": [contacts[0]["id"], contacts[0]["id"]],
            "emails": ["person2@example.com"],
            "message": "Meet us there",
        }
        first = client.post("/api/invites", json=payload)
        assert first.status_code == 200, first.text
        assert client.post("/api/invites", json=payload).json() == first.json()
        assert len(first.json()) == 1
        invite = first.json()[0]
        assert invite["channel"] == "in_app"
        assert invite["to_user_id"] == str(data.users[2])
        assert "sms_state" not in invite
    with data.client(2) as recipient:
        accepted = recipient.post(
            f"/api/invites/{invite['id']}/respond", json={"response": "accept"}
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
    with data.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM sms_delivery")) == 0
        assert (
            conn.scalar(
                text("SELECT state FROM attendance WHERE canonical_event_id=:event"),
                {"event": data.event},
            )
            == "attending"
        )


@pytest.mark.parametrize("phone", [None, "+14155552671"])
def test_unmatched_contact_rejects_entire_batch(
    contacts_data: InviteData, phone: str | None
) -> None:
    data = contacts_data
    with data.client(0) as client:
        response = client.post(
            "/api/contacts/import",
            json={"contacts": [{"email": "unregistered@example.com", "phone_e164": phone}]},
        )
        assert response.status_code == 200, response.text
        contact = client.get("/api/contacts").json()[0]
        response = client.post(
            "/api/invites",
            json={
                "canonical_event_id": str(data.event),
                "emails": ["person2@example.com"],
                "contact_ids": [contact["id"]],
            },
        )
        assert response.status_code == 422
        assert client.get("/api/invites/sent").json() == []
    with data.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM sms_delivery")) == 0


def test_concurrent_contact_invites_append_both_senders(contacts_data: InviteData) -> None:
    data = contacts_data
    contact_ids = []
    for sender in (0, 1):
        with data.client(sender) as client:
            contact_ids.append(imported(client)[0]["id"])

    def send(sender: int) -> dict[str, object]:
        with data.client(sender) as client:
            response = client.post(
                "/api/invites",
                json={
                    "canonical_event_id": str(data.event),
                    "contact_ids": [contact_ids[sender]],
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json()[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(send, [0, 1])
    assert first["id"] == second["id"]
    with data.client(2) as recipient:
        invites = recipient.get("/api/invites/received").json()
        assert len(invites) == 1
        assert set(invites[0]["invited_by"]) == {str(data.users[0]), str(data.users[1])}


def test_legacy_sms_history_is_private_inert_and_unique(contacts_data: InviteData) -> None:
    data = contacts_data
    invite_id = uuid4()
    with data.client(0) as owner:
        contact = imported(owner)[1]
        with data.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO invite (id, canonical_event_id, to_contact_id, invited_by, channel)
                VALUES (:id, :event, :contact, ARRAY[CAST(:sender AS uuid)], 'sms')
            """),
                {
                    "id": invite_id,
                    "event": data.event,
                    "contact": contact["id"],
                    "sender": data.users[0],
                },
            )
            conn.execute(
                text("""
                INSERT INTO sms_delivery (invite_id,phone_e164,body,state)
                VALUES (:id,'+14155552671','Historical body','pending')
            """),
                {"id": invite_id},
            )
        history = owner.get("/api/invites/sent").json()
        assert len(history) == 1 and history[0]["id"] == str(invite_id)
        assert history[0]["channel"] == "sms"
        assert not {"sms_state", "phone_e164", "body"} & history[0].keys()
        assert owner.post(f"/api/invites/{invite_id}/retry-sms").status_code == 404
        assert (
            owner.post(
                "/api/invites",
                json={
                    "canonical_event_id": str(data.event),
                    "emails": ["person2@example.com"],
                },
            ).status_code
            == 200
        )
    with data.client(1) as other:
        assert other.get("/api/invites/sent").json() == []
        assert other.get("/api/invites/received").json() == []
        assert (
            other.post(f"/api/invites/{invite_id}/respond", json={"response": "accept"}).status_code
            == 404
        )
    with data.engine.connect() as conn:
        assert conn.execute(
            text("""
            SELECT phone_e164,body,state,provider_sid,attempted_at
            FROM sms_delivery WHERE invite_id=:id
        """),
            {"id": invite_id},
        ).one() == ("+14155552671", "Historical body", "pending", None, None)
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO invite (id,canonical_event_id,to_contact_id,invited_by)
            VALUES (:id,:event,:contact,ARRAY[CAST(:sender AS uuid)])
        """),
            {"id": uuid4(), "event": data.event, "contact": contact["id"], "sender": data.users[0]},
        )


def test_google_consent_pages_and_browser_owner_binding(contacts_data: InviteData) -> None:
    replay = PeopleReplay()
    app.dependency_overrides[google_contacts] = replay.provider
    with contacts_data.client(0) as client:
        start = client.get("/api/contacts/google/start", follow_redirects=False)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        assert query["scope"] == ["https://www.googleapis.com/auth/contacts.readonly"]
        assert query["code_challenge_method"] == ["S256"]
        params = {"code": "contacts-code", "state": query["state"][0]}
        with contacts_data.client(1) as other:
            other.cookies.set(
                "__Host-event_oauth",
                client.cookies.get("__Host-event_oauth"),
                domain="api.example.test",
                path="/",
            )
            denied = other.get(
                "/api/contacts/google/callback", params=params, follow_redirects=False
            )
            assert "contacts_error" in denied.headers["location"]
            assert not replay.calls
        success = client.get("/api/contacts/google/callback", params=params, follow_redirects=False)
        assert success.headers["location"] == CONFIG.web_url + "/?contacts_imported=1"
        assert len(replay.calls) == 3
        assert len(client.get("/api/contacts").json()) == 2
        assert "fixture-access" not in str(client.cookies)
        assert "fixture-access" not in str(success.headers)


@pytest.mark.parametrize("failure", ["scope", "page"])
def test_google_failure_does_not_save_partial_contacts(
    contacts_data: InviteData, failure: str
) -> None:
    replay = PeopleReplay()
    replay.scope = "openid" if failure == "scope" else replay.scope
    replay.fail_second_page = failure == "page"
    app.dependency_overrides[google_contacts] = replay.provider
    with contacts_data.client(0) as client:
        start = client.get("/api/contacts/google/start", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        response = client.get(
            "/api/contacts/google/callback",
            follow_redirects=False,
            params={"code": "contacts-code", "state": state},
        )
        assert "contacts_error" in response.headers["location"]
        assert client.get("/api/contacts").json() == []


def test_signup_matches_existing_contact(contacts_data: InviteData) -> None:
    data = contacts_data
    with data.client(0) as client:
        client.post("/api/contacts/import", json={"contacts": [{"email": "person2@example.com"}]})
    with data.engine.begin() as conn:
        conn.execute(
            text("UPDATE contact SET matched_user_id=NULL WHERE owner_user_id=:id"),
            {"id": data.users[0]},
        )
    identity = GoogleIdentity(
        sub="invite-person-2",
        email="person2@example.com",
        email_verified=True,
        nonce="fixture",
        exp=int(NOW.timestamp()) + 3600,
        iat=int(NOW.timestamp()),
    )
    with data.engine.connect() as conn:
        save_google_user(conn, identity, NOW)
    with data.client(0) as client:
        assert client.get("/api/contacts").json()[0]["matched_user_id"] == str(data.users[2])


def test_matching_failure_rolls_back_and_retry_repairs_audit(
    contacts_data: InviteData,
    database_url: str,
) -> None:
    data = contacts_data
    with data.client(0) as client:
        imported(client)
    with data.engine.begin() as conn:
        conn.execute(text("UPDATE contact SET matched_user_id=NULL"))
        conn.execute(
            text("ALTER TABLE contact ADD CONSTRAINT fixture_match CHECK (matched_user_id IS NULL)")
        )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            match_contacts_to_users(
                database_url, airflow_run_id="contacts-failure", clock=lambda: NOW
            )
        with data.engine.connect() as conn:
            assert conn.execute(
                text("""
                SELECT status,error_message FROM ingest.run
                WHERE airflow_dag_id='match_contacts_to_users'
            """)
            ).one() == ("failed", "CheckViolation")
            assert (
                conn.scalar(text("SELECT count(*) FROM contact WHERE matched_user_id IS NOT NULL"))
                == 0
            )
    finally:
        with data.engine.begin() as conn:
            conn.execute(text("ALTER TABLE contact DROP CONSTRAINT fixture_match"))
    assert match_contacts_to_users(
        database_url,
        airflow_run_id="contacts-failure",
        clock=lambda: NOW,
    ) == {"matched": 1}
    with data.engine.connect() as conn:
        assert conn.execute(
            text("""
            SELECT status,error_message FROM ingest.run
            WHERE airflow_dag_id='match_contacts_to_users'
        """)
        ).one() == ("success", None)


def test_obsolete_phone_payload_does_not_partially_send(contacts_data: InviteData) -> None:
    with contacts_data.client(0) as client:
        result = client.post(
            "/api/invites",
            json={
                "canonical_event_id": str(contacts_data.event),
                "emails": ["person2@example.com"],
                "phones": ["+14155552671"],
            },
        )
        assert result.status_code == 422
        assert client.get("/api/invites/sent").json() == []
        assert client.get("/api/contacts").json() == []


def test_matching_job_phone_priority_and_graph_rebuild(
    contacts_data: InviteData,
    database_url: str,
    graph_environment: dict[str, str],
) -> None:
    data = contacts_data
    with data.client(0) as client:
        contacts = imported(client)
    with data.engine.begin() as conn:
        conn.execute(
            text("UPDATE app_user SET phone_e164='+14155552671' WHERE id=:id"),
            {"id": data.users[1]},
        )
        conn.execute(
            text("UPDATE contact SET email='person2@example.com' WHERE id=:id"),
            {"id": contacts[1]["id"]},
        )
    assert match_contacts_to_users(
        database_url, airflow_run_id="contacts-repeat", clock=lambda: NOW
    ) == {"matched": 1}
    assert match_contacts_to_users(
        database_url, airflow_run_id="contacts-repeat", clock=lambda: NOW
    ) == {"matched": 0}
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT matched_user_id FROM contact WHERE id=:id"), {"id": contacts[1]["id"]}
            )
            == data.users[1]
        )
        assert conn.execute(
            text("""
            SELECT status, events_found, started_at, finished_at FROM ingest.run
            WHERE airflow_dag_id='match_contacts_to_users'
        """)
        ).one() == ("success", 0, NOW, NOW)
    config = GraphConfig.from_env()
    with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
        for _ in range(2):
            project_to_neo4j(
                database_url, config, airflow_run_id="social-contacts", clock=lambda: NOW
            )
        with driver.session(database=config.database) as session:
            records = session.run(
                """
                MATCH (:User {id:$owner})-[:HAS_CONTACT]->(c:Contact)-[:IS_USER]->(u:User)
                RETURN c.id AS contact, u.id AS recipient
            """,
                owner=str(data.users[0]),
            ).data()
            assert {row["recipient"] for row in records} == {str(data.users[1]), str(data.users[2])}
            assert len(records) == 2
