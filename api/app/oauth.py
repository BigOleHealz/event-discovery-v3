"""Google authorization-code flow, with browser binding and locally verified ID tokens."""

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException, Request, Response
from itsdangerous import BadData, URLSafeSerializer
from pydantic import BaseModel, ConfigDict, Field, ValidationError


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    web_url: str
    secret: str
    secure: bool
    session_ttl: int
    refresh_ttl: int
    authorization_url: str
    token_url: str
    jwks_url: str


def auth_config() -> AuthConfig:
    required = (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "PUBLIC_WEB_BASE_URL",
        "SESSION_SIGNING_SECRET",
        "GOOGLE_OAUTH_AUTHORIZATION_URL",
        "GOOGLE_OAUTH_TOKEN_URL",
        "GOOGLE_OAUTH_JWKS_URL",
    )
    if any(not os.getenv(key, "").strip() for key in required):
        raise HTTPException(503, "Sign-in is not configured")
    try:
        ttl = int(os.getenv("SESSION_TTL_SECONDS", "900"))
        refresh = int(os.getenv("SESSION_REFRESH_TTL_SECONDS", "28800"))
        secure_setting = os.getenv("SESSION_COOKIE_SECURE", "true")
        if secure_setting not in {"true", "false"}:
            raise ValueError("Invalid secure-cookie setting")
        secure = secure_setting == "true"
        if len(os.environ["SESSION_SIGNING_SECRET"].encode()) < 32 or not 60 <= ttl <= refresh:
            raise ValueError("Invalid session settings")
        for key in (
            "PUBLIC_WEB_BASE_URL",
            "GOOGLE_OAUTH_REDIRECT_URI",
            "GOOGLE_OAUTH_AUTHORIZATION_URL",
            "GOOGLE_OAUTH_TOKEN_URL",
            "GOOGLE_OAUTH_JWKS_URL",
        ):
            url = urlsplit(os.environ[key])
            if not url.netloc or url.username or url.password or url.fragment or url.query:
                raise ValueError("Invalid auth URL")
            if url.scheme != "https" and not (
                not secure and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}
            ):
                raise ValueError("Auth URLs require HTTPS outside loopback development")
        if urlsplit(os.environ["PUBLIC_WEB_BASE_URL"]).path not in {"", "/"}:
            raise ValueError("Web URL must be an origin")
        return AuthConfig(
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            redirect_uri=os.environ["GOOGLE_OAUTH_REDIRECT_URI"],
            web_url=os.environ["PUBLIC_WEB_BASE_URL"].rstrip("/"),
            secret=os.environ["SESSION_SIGNING_SECRET"],
            secure=secure,
            session_ttl=ttl,
            refresh_ttl=refresh,
            authorization_url=os.environ["GOOGLE_OAUTH_AUTHORIZATION_URL"],
            token_url=os.environ["GOOGLE_OAUTH_TOKEN_URL"],
            jwks_url=os.environ["GOOGLE_OAUTH_JWKS_URL"],
        )
    except ValueError as error:
        raise HTTPException(503, "Sign-in configuration is invalid") from error


class OAuthAttempt(BaseModel):
    state: str
    nonce: str
    verifier: str
    issued_at: int
    expires_at: int
    owner_id: str | None = None
    purpose: str = "signin"


class GoogleIdentity(BaseModel):
    model_config = ConfigDict(strict=True)
    sub: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    email_verified: bool
    name: str | None = None
    picture: str | None = None
    nonce: str
    exp: int
    iat: int
    azp: str | None = None


def attempt_cookie(config: AuthConfig) -> str:
    return "__Host-event_oauth" if config.secure else "event_oauth"


def attempt_signer(config: AuthConfig) -> URLSafeSerializer:
    return URLSafeSerializer(config.secret, salt="event-discovery-oauth-v1")


def begin_oauth(
    response: Response, config: AuthConfig, now: datetime, *,
    scope: str = "openid email profile", owner_id: str | None = None,
    purpose: str = "signin",
) -> str:
    timestamp = int(now.timestamp())
    attempt = OAuthAttempt(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),
        issued_at=timestamp,
        expires_at=timestamp + 600,
        owner_id=owner_id, purpose=purpose,
    )
    response.set_cookie(
        attempt_cookie(config),
        attempt_signer(config).dumps(attempt.model_dump()),
        max_age=600,
        httponly=True,
        secure=config.secure,
        samesite="lax",
        path="/",
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest())
    return (
        config.authorization_url
        + "?"
        + urlencode(
            {
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "response_type": "code",
                "scope": scope,
                "state": attempt.state,
                "nonce": attempt.nonce,
                "code_challenge": challenge.rstrip(b"=").decode(),
                "code_challenge_method": "S256",
            }
        )
    )


def clear_attempt(response: Response, config: AuthConfig) -> None:
    response.delete_cookie(
        attempt_cookie(config),
        path="/",
        secure=config.secure,
        httponly=True,
        samesite="lax",
    )


def validate_attempt(
    request: Request,
    state: str,
    config: AuthConfig,
    now: datetime,
) -> OAuthAttempt:
    try:
        attempt = OAuthAttempt.model_validate(
            attempt_signer(config).loads(request.cookies.get(attempt_cookie(config), "")),
        )
    except (BadData, ValidationError) as error:
        raise HTTPException(400, "Invalid sign-in attempt") from error
    if (
        not secrets.compare_digest(attempt.state.encode(), state.encode())
        or not attempt.issued_at <= now.timestamp() < attempt.expires_at
    ):
        raise HTTPException(400, "Sign-in attempt expired or state did not match")
    return attempt


class GoogleOAuth:
    def __init__(self, config: AuthConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.transport = transport

    def exchange(self, code: str, attempt: OAuthAttempt, now: datetime) -> GoogleIdentity:
        try:
            with httpx.Client(transport=self.transport, timeout=10) as client:
                response = client.post(
                    self.config.token_url,
                    data={
                        "code": code,
                        "client_id": self.config.client_id,
                        "client_secret": self.config.client_secret,
                        "redirect_uri": self.config.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": attempt.verifier,
                    },
                )
                response.raise_for_status()
                token = response.json()["id_token"]
                keys_response = client.get(self.config.jwks_url)
                keys_response.raise_for_status()
                keys = jwt.PyJWKSet.from_dict(keys_response.json())
                kid = jwt.get_unverified_header(token)["kid"]
                key = next(key for key in keys.keys if key.key_id == kid)
                claims = jwt.decode(
                    token,
                    key.key,
                    algorithms=["RS256"],
                    audience=self.config.client_id,
                    issuer=["https://accounts.google.com", "accounts.google.com"],
                    options={
                        "require": ["sub", "aud", "iss", "exp", "iat", "nonce"],
                        # Time is verified against the injected clock below.
                        "verify_exp": False,
                        "verify_iat": False,
                        "verify_nbf": False,
                    },
                )
                identity = GoogleIdentity.model_validate(claims)
                if (
                    not identity.email_verified
                    or not secrets.compare_digest(identity.nonce, attempt.nonce)
                    or not identity.iat <= now.timestamp() < identity.exp
                    or (identity.azp is not None and identity.azp != self.config.client_id)
                    or (
                        isinstance(claims["aud"], list)
                        and len(claims["aud"]) > 1
                        and identity.azp != self.config.client_id
                    )
                    or ("nbf" in claims and float(claims["nbf"]) > now.timestamp())
                ):
                    raise ValueError("Invalid identity claims")
                return identity
        except httpx.HTTPStatusError as error:
            if error.response.status_code >= 500:
                raise HTTPException(502, "Google sign-in is temporarily unavailable") from error
            raise HTTPException(401, "Google sign-in was rejected") from error
        except httpx.RequestError as error:
            raise HTTPException(502, "Google sign-in is temporarily unavailable") from error
        except (jwt.PyJWTError, ValueError, KeyError, TypeError, StopIteration) as error:
            raise HTTPException(401, "Google identity could not be verified") from error
