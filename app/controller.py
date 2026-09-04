from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

from app.database import Database, utc_now
from app.drivers.base import Driver
from app.events import EventBus
from app.models import CommandRequest, CommandResult, DeviceState, DeviceView


class Controller:
    def __init__(
        self,
        driver: Driver,
        database: Database,
        events: EventBus,
        dedupe_seconds: int,
    ) -> None:
        self.driver = driver
        self.database = database
        self.events = events
        self.dedupe_seconds = dedupe_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._state_cache: dict[str, DeviceState] = {}

    async def list_devices(self) -> list[DeviceView]:
        devices = await self.driver.list_devices()
        result: list[DeviceView] = []
        for device in devices:
            try:
                state = await self.driver.get_state(device.id)
                self._state_cache[device.id] = state
            except Exception as exc:
                cached = self._state_cache.get(device.id)
                if cached:
                    state = cached.model_copy(update={"stale": True, "error": type(exc).__name__})
                else:
                    raise
            result.append(DeviceView(**device.model_dump(), state=state))
        return result

    async def get_device(self, device_id: str) -> DeviceView:
        devices = await self.driver.list_devices()
        device = next((item for item in devices if item.id == device_id), None)
        if not device:
            raise KeyError(device_id)
        state = await self.driver.get_state(device_id)
        self._state_cache[device_id] = state
        return DeviceView(**device.model_dump(), state=state)

    async def command(
        self, device_id: str, command: CommandRequest, *, source: str = "api"
    ) -> CommandResult:
        lock = self._locks.setdefault(device_id, asyncio.Lock())
        fingerprint = hashlib.sha256(
            json.dumps(command.model_dump(), separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        async with lock:
            seen = await self.database.command_seen_recently(
                device_id,
                fingerprint,
                utc_now() - timedelta(seconds=self.dedupe_seconds),
            )
            if seen:
                return CommandResult(
                    accepted=False,
                    device_id=device_id,
                    operation=command.operation,
                    message="Duplicate command suppressed",
                )
            audit_id = await self.database.audit_start(
                device_id, command.operation, fingerprint, source
            )
            try:
                result = await self.driver.send_command(device_id, command)
            except Exception as exc:
                await self.database.audit_finish(audit_id, False, type(exc).__name__)
                raise
            await self.database.audit_finish(audit_id, result.accepted, result.message[:160])
            if result.state:
                self._state_cache[device_id] = result.state
            await self.events.publish("device", result.model_dump(mode="json"))
            return result
