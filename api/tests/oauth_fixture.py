"""Hand-written provider replay. Keys are ephemeral and never leave the test process."""

import json
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.oauth import AuthConfig, GoogleOAuth, OAuthAttempt


class GoogleReplay:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict[str, object] = {}
        self.calls: list[httpx.Request] = []
        self.status = 200
        self.invalid_signature = False

    def provider(self, config: AuthConfig, attempt: OAuthAttempt, now: datetime) -> GoogleOAuth:
        claims: dict[str, object] = {
            "sub": "google-person-one",
            "email": "friend@example.test",
            "email_verified": True,
            "name": "Test Friend",
            "picture": "https://images.example.test/avatar.png",
            "aud": config.client_id,
            "iss": "https://accounts.google.com",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 3600,
            "nonce": attempt.nonce,
        }
        claims.update(self.claims)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk["kid"] = "fixture-key"
        signing_key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if self.invalid_signature
            else self.key
        )
        token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "fixture-key"})

        def replay(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            if str(request.url) == config.token_url:
                form = parse_qs(request.content.decode())
                assert form["code"] == ["fixture-code"]
                assert form["client_secret"] == [config.client_secret]
                assert form["redirect_uri"] == [config.redirect_uri]
                assert form["code_verifier"] == [attempt.verifier]
                return httpx.Response(self.status, json={"id_token": token})
            assert str(request.url) == config.jwks_url
            return httpx.Response(200, json={"keys": [jwk]})

        return GoogleOAuth(config, transport=httpx.MockTransport(replay))
