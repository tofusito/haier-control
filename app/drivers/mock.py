from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.drivers.base import UnsupportedCapability
from app.models import (
    AdvancedCapability,
    CommandRequest,
    CommandResult,
    DeviceCapabilities,
    DeviceMode,
    DeviceState,
    DeviceSummary,
)


def now() -> datetime:
    return datetime.now(UTC)


class MockDriver:
    name = "mock"

    def __init__(self) -> None:
        capabilities = DeviceCapabilities(
            modes=list(DeviceMode),
            temperature_min=16,
            temperature_max=30,
            temperature_step=0.5,
            fan_modes=["auto", "low", "medium", "high"],
            vertical_swing=["fixed", "swing"],
            horizontal_swing=["fixed", "swing"],
            advanced=[
                AdvancedCapability(key="eco", label="Eco", kind="toggle"),
                AdvancedCapability(key="sleep", label="Sueño", kind="toggle"),
                AdvancedCapability(key="rapid", label="Turbo", kind="toggle"),
                AdvancedCapability(key="display", label="Display", kind="toggle"),
                AdvancedCapability(key="health", label="Health", kind="toggle"),
                AdvancedCapability(key="mute", label="Silencio", kind="toggle"),
            ],
        )
        self._devices = {
            "salon": DeviceSummary(
                id="salon", name="Salón", model="Haier AC (simulado)", capabilities=capabilities
            ),
            "dormitorio": DeviceSummary(
                id="dormitorio",
                name="Dormitorio",
                model="Haier AC (simulado)",
                capabilities=capabilities,
            ),
        }
        self._states = {
            "salon": DeviceState(
                device_id="salon",
                online=True,
                power=True,
                mode=DeviceMode.COOL,
                target_temperature=24,
                room_temperature=26.3,
                fan_mode="auto",
                vertical_swing="swing",
                horizontal_swing="fixed",
                advanced={"eco": True, "sleep": False, "rapid": False, "display": True},
                updated_at=now(),
            ),
            "dormitorio": DeviceState(
                device_id="dormitorio",
                online=True,
                power=False,
                mode=DeviceMode.AUTO,
                target_temperature=23,
                room_temperature=24.1,
                fan_mode="low",
                vertical_swing="fixed",
                horizontal_swing="fixed",
                advanced={"eco": False, "sleep": True, "rapid": False, "display": False},
                updated_at=now(),
            ),
        }
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_devices(self) -> list[DeviceSummary]:
        return list(self._devices.values())

    async def get_state(self, device_id: str) -> DeviceState:
        try:
            return self._states[device_id].model_copy(deep=True)
        except KeyError:
            raise UnsupportedCapability("Unknown device") from None

    async def send_command(self, device_id: str, command: CommandRequest) -> CommandResult:
        async with self._lock:
            state = await self.get_state(device_id)
            caps = self._devices[device_id].capabilities
            self._apply(state, caps, command)
            state.updated_at = now()
            self._states[device_id] = state
            return CommandResult(
                accepted=True,
                device_id=device_id,
                operation=command.operation,
                state=state,
                message="Command accepted by MockDriver",
            )

    @staticmethod
    def _apply(state: DeviceState, caps: DeviceCapabilities, command: CommandRequest) -> None:
        value: Any = command.value
        match command.operation:
            case "power":
                if not isinstance(value, bool):
                    raise UnsupportedCapability("power expects a boolean")
                state.power = value
            case "set_mode":
                mode = DeviceMode(value)
                if mode not in caps.modes:
                    raise UnsupportedCapability("Mode is not supported")
                state.mode = mode
                state.power = True
            case "set_temperature":
                numeric = float(value)
                if caps.temperature_min is None or caps.temperature_max is None:
                    raise UnsupportedCapability("Temperature control is unavailable")
                if not caps.temperature_min <= numeric <= caps.temperature_max:
                    raise UnsupportedCapability("Temperature is outside the device range")
                state.target_temperature = numeric
            case "set_fan":
                if str(value) not in caps.fan_modes:
                    raise UnsupportedCapability("Fan mode is not supported")
                state.fan_mode = str(value)
            case "set_vertical_swing":
                if str(value) not in caps.vertical_swing:
                    raise UnsupportedCapability("Vertical swing is not supported")
                state.vertical_swing = str(value)
            case "set_horizontal_swing":
                if str(value) not in caps.horizontal_swing:
                    raise UnsupportedCapability("Horizontal swing is not supported")
                state.horizontal_swing = str(value)
            case "set_advanced":
                allowed = {item.key for item in caps.advanced}
                if command.key not in allowed:
                    raise UnsupportedCapability("Advanced capability is not supported")
                state.advanced[command.key] = value
