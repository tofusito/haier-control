from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.database import Database
from app.drivers.haier_auth import HaierAuthenticationError, HaierTokens
from app.security import token_hash
from app.setup_flow import SetupFlowManager

TOKENS = HaierTokens("access", "refresh", "identity", "cognito", "mobile")


class FakeDriver:
    def __init__(self) -> None:
        self.requires_reauth = True
        self.tokens: HaierTokens | None = None

    def store_tokens(self, tokens: HaierTokens) -> None:
        self.tokens = tokens
        self.requires_reauth = False


class FakeSession:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.closed = False
        self.resends = 0

    async def begin(self, email: str, password: str) -> HaierTokens | None:
        if self.mode == "reject":
            raise HaierAuthenticationError("rejected")
        return None if self.mode == "mfa" else TOKENS

    async def submit_code(self, code: str) -> HaierTokens:
        if code != "123456":
            raise HaierAuthenticationError("invalid")
        return TOKENS

    async def resend_code(self) -> None:
        self.resends += 1

    async def close(self) -> None:
        self.closed = True


async def manager(tmp_path: Path, mode: str) -> tuple[SetupFlowManager, FakeDriver, FakeSession]:
    database = Database(tmp_path / f"{mode}.db")
    await database.initialize()
    driver = FakeDriver()
    session = FakeSession(mode)
    value = SetupFlowManager(
        driver,  # type: ignore[arg-type]
        database,
        b"k" * 40,
        "client",
        pairing_ttl=10,
        flow_ttl=10,
        session_factory=lambda: session,  # type: ignore[arg-type,return-value]
    )
    value._pairing_digest = token_hash("pairing-token-that-is-long", b"k" * 40)
    value._pairing_expires_at = time.monotonic() + 10
    return value, driver, session


@pytest.mark.asyncio
async def test_non_mfa_setup_completes_and_returns_first_api_token(tmp_path: Path) -> None:
    flow, driver, session = await manager(tmp_path, "success")
    result = await flow.begin(
        "pairing-token-that-is-long", "person@example.invalid", "password"
    )
    assert result.status == "complete"
    assert result.api_token and result.api_token.startswith("hc_")
    assert driver.tokens == TOKENS
    assert session.closed is True


@pytest.mark.asyncio
async def test_mfa_state_machine_and_csrf(tmp_path: Path) -> None:
    flow, driver, session = await manager(tmp_path, "mfa")
    pending = await flow.begin(
        "pairing-token-that-is-long", "person@example.invalid", "password"
    )
    assert pending.status == "mfa_required"
    with pytest.raises(HaierAuthenticationError, match="invalid"):
        await flow.submit_otp(pending.flow_id or "", "wrong-csrf", "123456")
    with pytest.raises(HaierAuthenticationError, match="invalid"):
        await flow.submit_otp(pending.flow_id or "", pending.csrf_token or "", "000000")
    complete = await flow.submit_otp(
        pending.flow_id or "", pending.csrf_token or "", "123456"
    )
    assert complete.status == "complete"
    assert driver.tokens == TOKENS
    assert session.closed is True


@pytest.mark.asyncio
async def test_pairing_token_is_single_use_after_rejected_credentials(tmp_path: Path) -> None:
    flow, _driver, _session = await manager(tmp_path, "reject")
    with pytest.raises(HaierAuthenticationError, match="rejected"):
        await flow.begin(
            "pairing-token-that-is-long", "person@example.invalid", "wrong"
        )
    with pytest.raises(HaierAuthenticationError, match="invalid or expired"):
        await flow.begin(
            "pairing-token-that-is-long", "person@example.invalid", "wrong"
        )


@pytest.mark.asyncio
async def test_expired_pairing_is_rejected(tmp_path: Path) -> None:
    flow, _driver, _session = await manager(tmp_path, "success")
    flow._pairing_expires_at = time.monotonic() - 1
    with pytest.raises(HaierAuthenticationError, match="invalid or expired"):
        await flow.begin(
            "pairing-token-that-is-long", "person@example.invalid", "password"
        )


@pytest.mark.asyncio
async def test_resend_is_bounded(tmp_path: Path) -> None:
    flow, _driver, session = await manager(tmp_path, "mfa")
    pending = await flow.begin(
        "pairing-token-that-is-long", "person@example.invalid", "password"
    )
    await flow.resend(pending.flow_id or "", pending.csrf_token or "")
    await flow.resend(pending.flow_id or "", pending.csrf_token or "")
    with pytest.raises(HaierAuthenticationError, match="limit"):
        await flow.resend(pending.flow_id or "", pending.csrf_token or "")
    assert session.resends == 2
