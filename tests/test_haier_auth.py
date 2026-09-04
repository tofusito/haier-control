from __future__ import annotations

import httpx
import pytest
from fastapi import status

from app.drivers.haier_auth import (
    AUTH_USER_AGENT,
    HaierAuthenticationError,
    HaierLoginNotAccepted,
    HaierPairingTokenError,
    HaierProtocolError,
    InteractiveHaierLogin,
    _aura_redirect,
    _is_progressive_otp,
    _progressive_navigation_target,
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


def test_aura_redirect_uses_events_url_when_present() -> None:
    payload = {"events": [{"attributes": {"values": {"url": "/finaltok?x=1"}}}]}

    assert _aura_redirect(payload) == "/finaltok?x=1"


def test_aura_redirect_accepts_a_navigable_return_value() -> None:
    payload = {"actions": [{"state": "SUCCESS", "returnValue": "/finaltok?x=1"}]}

    assert _aura_redirect(payload) == "/finaltok?x=1"


def test_aura_redirect_flags_a_rejected_login() -> None:
    payload = {
        "actions": [
            {"state": "SUCCESS", "returnValue": "invalid username or password combination"}
        ]
    }

    with pytest.raises(HaierLoginNotAccepted):
        _aura_redirect(payload)


def test_aura_redirect_reports_a_safe_shape_for_success_without_redirect() -> None:
    """Reproduces the real symptom: Salesforce accepts the login (action state
    SUCCESS) but the response carries no events/redirect. The diagnostic shape
    embedded in the exception message must stay structural (no PII) so it can be
    logged safely, while the client-facing detail (tested above) never includes it.
    """
    payload = {"actions": [{"state": "SUCCESS", "returnValue": None}]}

    with pytest.raises(HaierProtocolError) as excinfo:
        _aura_redirect(payload)

    message = str(excinfo.value)
    assert "action_state': 'SUCCESS'" in message
    assert "event_count': None" in message


def test_progressive_login_is_recognised_as_email_otp() -> None:
    page = (
        "<html>ProgressiveLoginController verifyEmailOTP "
        "<input name='emailcode' type='text'></html>"
    )

    assert _is_progressive_otp(page) is True


def test_progressive_login_without_otp_markers_falls_back_to_its_own_link() -> None:
    """Reproduces the real symptom: Salesforce sends a real, correct login to a
    ProgressiveLogin page that is NOT an OTP challenge (no 2FA on the account).
    The old code treated every ProgressiveLogin redirect as OTP and hard-failed
    here instead of following this page's own next-hop link, like addhOn does.
    """
    page = '<html><body>one moment <a href="/finaltok?x=1">continue</a></body></html>'

    assert _is_progressive_otp(page) is False
    assert _progressive_navigation_target(page) == "/finaltok?x=1"


def test_progressive_navigation_target_accepts_an_empty_href() -> None:
    page = '<a href="">continue</a>'

    assert _progressive_navigation_target(page) == ""


def test_progressive_navigation_target_is_none_without_any_link() -> None:
    page = "<html><body>no link here</body></html>"

    assert _progressive_navigation_target(page) is None
