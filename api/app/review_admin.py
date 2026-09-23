"""Provision the configured pre-OAuth reviewer on API startup, after migrations."""

import os
from uuid import UUID

from sqlalchemy import text

from app.database import get_engine


def main() -> None:
    reviewer_id = os.environ.get("ADMIN_REVIEW_USER_ID", "")
    if not reviewer_id or not os.environ.get("ADMIN_REVIEW_TOKEN", ""):
        return  # Review access is disabled unless both settings are present.
    reviewer = UUID(reviewer_id)
    with get_engine().begin() as connection:
        connection.execute(text("""
            INSERT INTO app_user (id, display_name, is_shadow)
            VALUES (:id, 'Dedup reviewer', true) ON CONFLICT (id) DO NOTHING
        """), {"id": reviewer})
    print(f"Reviewer {reviewer} is ready")


if __name__ == "__main__":
    main()
