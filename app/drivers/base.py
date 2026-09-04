from __future__ import annotations

from typing import Protocol

from app.models import CommandRequest, CommandResult, DeviceState, DeviceSummary


class DriverError(RuntimeError):
    """An honest, user-visible driver failure."""


class DriverUnavailable(DriverError):
    """The configured driver cannot currently reach its backend."""


class UnsupportedCapability(DriverError):
    """The device did not advertise the requested capability."""


class Driver(Protocol):
    name: str

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def list_devices(self) -> list[DeviceSummary]: ...

    async def get_state(self, device_id: str) -> DeviceState: ...

    async def send_command(self, device_id: str, command: CommandRequest) -> CommandResult: ...
