"""Twilio submission with durable claims; ambiguous attempts are never auto-retried."""

import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import Connection, text

from app.contact_import import normalize_phone


@dataclass(frozen=True)
class SMSConfig:
    account_sid: str
    auth_token: str
    from_number: str
    messages_url: str


def sms_config() -> SMSConfig:
    try:
        config = SMSConfig(
            account_sid=os.environ["TWILIO_ACCOUNT_SID"],
            auth_token=os.environ["TWILIO_AUTH_TOKEN"],
            from_number=normalize_phone(os.environ["TWILIO_FROM_NUMBER"]),
            messages_url=os.environ["TWILIO_MESSAGES_URL"],
        )
        url = urlsplit(config.messages_url)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.fragment
            or url.query
            or not config.account_sid
            or not config.auth_token
        ):
            raise ValueError("Invalid SMS configuration")
        return config
    except (KeyError, ValueError) as error:
        raise HTTPException(503, "SMS invitations are not configured") from error


class TwilioSMS:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport

    def submit(self, config: SMSConfig, phone: str, body: str) -> tuple[str, str | None]:
        try:
            with httpx.Client(transport=self.transport, timeout=15) as client:
                response = client.post(
                    config.messages_url,
                    auth=(config.account_sid, config.auth_token),
                    data={"To": phone, "From": config.from_number, "Body": body},
                )
                if 400 <= response.status_code < 500:
                    return "failed", None
                response.raise_for_status()
                data = response.json()
                sid = data["sid"]
                if not isinstance(sid, str) or not sid.startswith("SM") or len(sid) != 34:
                    return "unknown", None
                if data.get("status") in {"failed", "undelivered", "canceled"}:
                    return "failed", sid
                return "submitted", sid
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # The provider may have accepted a request before the connection failed.
            return "unknown", None

    def deliver(
        self,
        connection: Connection,
        invite_id: UUID,
        config: SMSConfig,
        now: datetime,
    ) -> None:
        row = (
            connection.execute(
                text("""
            UPDATE sms_delivery SET state='sending', attempted_at=:now
            WHERE invite_id=:id AND state='pending' RETURNING phone_e164, body
        """),
                {"id": invite_id, "now": now},
            )
            .mappings()
            .one_or_none()
        )
        connection.commit()
        if row is None:
            return
        state, sid = self.submit(config, row["phone_e164"], row["body"])
        connection.execute(
            text("""
            UPDATE sms_delivery SET state=:state, provider_sid=:sid WHERE invite_id=:id
        """),
            {"id": invite_id, "state": state, "sid": sid},
        )
        connection.commit()


def twilio_sms() -> TwilioSMS:
    return TwilioSMS()
