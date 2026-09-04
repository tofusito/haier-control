from __future__ import annotations

import httpx
import pytest
from fastapi import status

from app.drivers.haier_auth import (
    AUTH_USER_AGENT,
    HaierAuthenticationError,
    HaierPairingTokenError,
    HaierProtocolError,
    InteractiveHaierLogin,
)
from app.main import _setup_failure


@pytest.mark.asyncio
async def test_interactive_login_uses_compatible_salesforce_user_agent() -> None:
    session = InteractiveHaierLogin("client")
    try:
        assert session.client.headers["user-agent"] == AUTH_USER_AGENT
    finally:
        await session.close()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_category"),
    [
        (HaierPairingTokenError("expired"), status.HTTP_400_BAD_REQUEST, "pairing_token"),
        (HaierProtocolError("schema"), status.HTTP_502_BAD_GATEWAY, "protocol"),
        (httpx.ConnectError("offline"), status.HTTP_502_BAD_GATEWAY, "network"),
        (HaierAuthenticationError("rejected"), status.HTTP_400_BAD_REQUEST, "authentication"),
        (RuntimeError("unexpected"), status.HTTP_500_INTERNAL_SERVER_ERROR, "unexpected"),
    ],
)
def test_setup_failure_categories_are_safe_and_actionable(
    error: Exception, expected_status: int, expected_category: str
) -> None:
    response_status, detail, category = _setup_failure(error)

    assert response_status == expected_status
    assert category == expected_category
    assert str(error) not in detail
