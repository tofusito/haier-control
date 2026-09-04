from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.database import Database
from app.drivers.haier_auth import HaierAuthenticationError, HaierTokens, InteractiveHaierLogin
from app.drivers.haier_cloud import HaierCloudDriver
from app.models import HaierSetupResponse
from app.security import new_api_token, token_hash

_LOGGER = logging.getLogger(__name__)


@dataclass
class PendingFlow:
    session: InteractiveHaierLogin
    csrf_token: str
    expires_at: float
    attempts: int = 0
    resend_count: int = 1


class SetupFlowManager:
    """In-memory, one-use pairing flow. No credential is persisted or logged."""

    def __init__(
        self,
        driver: HaierCloudDriver | None,
        database: Database,
        master_key: bytes,
        client_id: str,
        *,
        pairing_ttl: int = 600,
        flow_ttl: int = 600,
        session_factory: Callable[[], InteractiveHaierLogin] | None = None,
    ) -> None:
        self.driver = driver
        self.database = database
        self.master_key = master_key
        self.client_id = client_id
        self.pairing_ttl = pairing_ttl
        self.flow_ttl = flow_ttl
        self._session_factory = session_factory or (lambda: InteractiveHaierLogin(client_id))
        self._pairing_digest: str | None = None
        self._pairing_expires_at = 0.0
        self._flows: dict[str, PendingFlow] = {}
        self._lock = asyncio.Lock()

    @property
    def setup_required(self) -> bool:
        return bool(self.driver and self.driver.requires_reauth)

    def ensure_pairing(self) -> None:
        if not self.setup_required or time.monotonic() < self._pairing_expires_at:
            return
        token = secrets.token_urlsafe(24)
        self._pairing_digest = token_hash(token, self.master_key)
        self._pairing_expires_at = time.monotonic() + self.pairing_ttl
        _LOGGER.warning(
            "Haier setup pairing token (expires in %d seconds, one use): %s",
            self.pairing_ttl,
            token,
        )

    async def begin(self, pairing_token: str, email: str, password: str) -> HaierSetupResponse:
        async with self._lock:
            self.ensure_pairing()
            if not self.setup_required:
                raise HaierAuthenticationError("Haier setup is not currently required")
            if (
                not self._pairing_digest
                or time.monotonic() >= self._pairing_expires_at
                or not secrets.compare_digest(
                    token_hash(pairing_token, self.master_key), self._pairing_digest
                )
            ):
                raise HaierAuthenticationError("Pairing token is invalid or expired")
            self._pairing_digest = None
            self._pairing_expires_at = 0.0
        session = self._session_factory()
        try:
            result = await session.begin(email, password)
        finally:
            password = ""  # noqa: F841 - never retained after the handshake call
        if result:
            try:
                return await self._complete(result)
            finally:
                await session.close()
        flow_id = secrets.token_urlsafe(24)
        csrf = secrets.token_urlsafe(24)
        self._flows[flow_id] = PendingFlow(
            session=session,
            csrf_token=csrf,
            expires_at=time.monotonic() + self.flow_ttl,
        )
        return HaierSetupResponse(
            status="mfa_required",
            flow_id=flow_id,
            csrf_token=csrf,
            expires_in=self.flow_ttl,
            message="A verification code was sent by email",
        )

    async def submit_otp(self, flow_id: str, csrf_token: str, code: str) -> HaierSetupResponse:
        flow = await self._flow(flow_id, csrf_token)
        flow.attempts += 1
        if flow.attempts > 5:
            await self._discard(flow_id)
            raise HaierAuthenticationError("Too many OTP attempts; start pairing again")
        try:
            tokens = await flow.session.submit_code(code)
        except HaierAuthenticationError:
            if flow.attempts >= 5:
                await self._discard(flow_id)
            raise
        try:
            return await self._complete(tokens)
        finally:
            await self._discard(flow_id)

    async def resend(self, flow_id: str, csrf_token: str) -> None:
        flow = await self._flow(flow_id, csrf_token)
        if flow.resend_count >= 3:
            raise HaierAuthenticationError("Verification code resend limit reached")
        await flow.session.resend_code()
        flow.resend_count += 1

    async def _complete(self, tokens: HaierTokens) -> HaierSetupResponse:
        if not self.driver:
            raise HaierAuthenticationError("Haier Cloud driver is not enabled")
        self.driver.store_tokens(tokens)
        api_token: str | None = None
        if await self.database.token_count() == 0:
            api_token = new_api_token()
            await self.database.create_token(
                "web-setup",
                token_hash(api_token, self.master_key),
                {"read", "control", "timers"},
            )
        return HaierSetupResponse(
            status="complete",
            api_token=api_token,
            message="hOn session encrypted; setup endpoint is now disabled",
        )

    async def _flow(self, flow_id: str, csrf_token: str) -> PendingFlow:
        flow = self._flows.get(flow_id)
        if not flow or not secrets.compare_digest(csrf_token, flow.csrf_token):
            raise HaierAuthenticationError("Setup flow is invalid")
        if time.monotonic() >= flow.expires_at:
            await self._discard(flow_id)
            raise HaierAuthenticationError("Setup flow expired")
        return flow

    async def _discard(self, flow_id: str) -> None:
        flow = self._flows.pop(flow_id, None)
        if flow:
            await flow.session.close()

    async def close(self) -> None:
        for flow_id in list(self._flows):
            await self._discard(flow_id)
