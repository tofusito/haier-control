from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DeviceMode(StrEnum):
    AUTO = "auto"
    COOL = "cool"
    HEAT = "heat"
    DRY = "dry"
    FAN = "fan"


class AdvancedCapability(BaseModel):
    key: str
    label: str
    kind: Literal["toggle", "enum", "number"]
    options: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


class DeviceCapabilities(BaseModel):
    modes: list[DeviceMode]
    temperature_min: float | None = None
    temperature_max: float | None = None
    temperature_step: float | None = None
    fan_modes: list[str] = Field(default_factory=list)
    vertical_swing: list[str] = Field(default_factory=list)
    horizontal_swing: list[str] = Field(default_factory=list)
    advanced: list[AdvancedCapability] = Field(default_factory=list)


class DeviceSummary(BaseModel):
    id: str
    name: str
    model: str | None = None
    capabilities: DeviceCapabilities


class DeviceState(BaseModel):
    device_id: str
    online: bool
    stale: bool = False
    power: bool | None = None
    mode: DeviceMode | None = None
    target_temperature: float | None = None
    room_temperature: float | None = None
    fan_mode: str | None = None
    vertical_swing: str | None = None
    horizontal_swing: str | None = None
    advanced: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
    error: str | None = None


class DeviceView(DeviceSummary):
    state: DeviceState


class CommandRequest(BaseModel):
    operation: Literal[
        "power",
        "set_mode",
        "set_temperature",
        "set_fan",
        "set_vertical_swing",
        "set_horizontal_swing",
        "set_advanced",
    ]
    value: Any
    key: str | None = None

    @model_validator(mode="after")
    def advanced_requires_key(self) -> CommandRequest:
        if self.operation == "set_advanced" and not self.key:
            raise ValueError("key is required for set_advanced")
        return self


class CommandResult(BaseModel):
    accepted: bool
    device_id: str
    operation: str
    state: DeviceState | None = None
    message: str


class TimerAction(StrEnum):
    ON = "on"
    OFF = "off"


class TimerStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class TimerCreate(BaseModel):
    device_id: str
    action: TimerAction
    execute_at: datetime
    command: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class TimerUpdate(BaseModel):
    execute_at: datetime | None = None
    command: dict[str, Any] | None = None


class TimerView(BaseModel):
    id: str
    device_id: str
    action: TimerAction
    execute_at: datetime
    status: TimerStatus
    command: dict[str, Any]
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    executed_at: datetime | None = None
    error: str | None = None


class TokenBootstrapRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: set[Literal["read", "control", "timers"]]


class TokenBootstrapResponse(BaseModel):
    token: str
    name: str
    scopes: list[str]
    warning: str = "This token is shown once. Store it securely."


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    driver: str
    database: Literal["ok", "error"]
    scheduler: Literal["ok", "error"]
    setup_required: bool = False
    trusted_network: bool = False


class HaierSetupStart(BaseModel):
    pairing_token: str = Field(min_length=20, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class HaierSetupOtp(BaseModel):
    flow_id: str = Field(min_length=20, max_length=200)
    csrf_token: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=4, max_length=12, pattern=r"^[0-9]+$")


class HaierSetupResend(BaseModel):
    flow_id: str = Field(min_length=20, max_length=200)
    csrf_token: str = Field(min_length=20, max_length=200)


class HaierSetupResponse(BaseModel):
    status: Literal["mfa_required", "complete"]
    flow_id: str | None = None
    csrf_token: str | None = None
    expires_in: int | None = None
    api_token: str | None = None
    message: str


class HaierSetupStatusResponse(BaseModel):
    status: Literal["manual", "mfa_required", "complete", "failed"]
    flow_id: str | None = None
    csrf_token: str | None = None
    expires_in: int | None = None
    api_token: str | None = None
    message: str | None = None
