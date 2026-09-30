"""Bounded contact parsing. International numbers avoid guessing a user's country."""

import phonenumbers
import vobject  # type: ignore[import-untyped]
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def normalize_phone(value: str) -> str:
    try:
        phone = phonenumbers.parse(value.removeprefix("tel:"), None)
        if phone.extension or not phonenumbers.is_valid_number(phone):
            raise ValueError("Invalid phone number")
        return str(phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164))
    except phonenumbers.NumberParseException as error:
        raise ValueError("Use an international phone number, including +country code") from error


class ContactInput(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    phone_e164: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None

    @field_validator("phone_e164")
    @classmethod
    def phone(cls, value: str | None) -> str | None:
        return normalize_phone(value) if value else None

    @field_validator("email")
    @classmethod
    def email_lower(cls, value: str | None) -> str | None:
        return value.casefold() if value else None

    @model_validator(mode="after")
    def reachable(self) -> "ContactInput":
        if not self.phone_e164 and not self.email:
            raise ValueError("A contact needs a phone number or email")
        return self


def parse_vcard(value: str) -> list[ContactInput]:
    contacts: list[ContactInput] = []
    try:
        for card in vobject.readComponents(value):
            if card.name != "VCARD":
                raise ValueError("Only vCards are supported")
            # Each number is independently selectable; keep the first email on each row.
            phones: list[str | None] = [str(item.value) for item in card.contents.get("tel", [])]
            emails = [str(item.value) for item in card.contents.get("email", [])]
            names = card.contents.get("fn", [])
            name = str(names[0].value) if names else None
            if not phones and not emails:
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
                raise ValueError("Import at most 2,000 contact addresses at a time")
    except (vobject.base.ParseError, AttributeError, IndexError) as error:
        raise ValueError("Invalid vCard file") from error
    if not contacts:
        raise ValueError("No phone numbers or emails found")
    return contacts
