"""Temporary admin authentication until the phase 6 session implementation."""

import os
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    token = os.environ.get("ADMIN_REVIEW_TOKEN", "")
    reviewer = os.environ.get("ADMIN_REVIEW_USER_ID", "")
    if not token or not reviewer:
        raise HTTPException(503, "Admin review is not configured")
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), token.encode()
    ):
        raise HTTPException(401, "Admin token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return UUID(reviewer)
    except ValueError as error:
        raise HTTPException(503, "Admin reviewer ID is invalid") from error
