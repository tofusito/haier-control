from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.controller import Controller
from app.database import Database
from app.drivers.mock import MockDriver
from app.events import EventBus
from app.models import TimerAction, TimerCreate, TimerStatus
from app.scheduler import TimerScheduler


@pytest.mark.asyncio
async def test_due_timer_executes_with_browser_closed(tmp_path: Path) -> None:
    database = Database(tmp_path / "timers.db")
    await database.initialize()
    driver = MockDriver()
    events = EventBus()
    controller = Controller(driver, database, events, dedupe_seconds=1)
    scheduler = TimerScheduler(database, controller, events)
    timer = await database.create_timer(
        TimerCreate(
            device_id="dormitorio",
            action=TimerAction.ON,
            execute_at=datetime.now(UTC) - timedelta(seconds=1),
            command={"mode": "heat", "temperature": 21, "fan_mode": "medium"},
        )
    )
    await scheduler.start()
    for _ in range(30):
        updated = await database.get_timer(timer.id)
        if updated and updated.status == TimerStatus.EXECUTED:
            break
        await asyncio.sleep(0.05)
    await scheduler.stop()
    state = await driver.get_state("dormitorio")
    assert updated is not None
    assert updated.status == TimerStatus.EXECUTED
    assert state.power is True
    assert state.mode == "heat"
    assert state.target_temperature == 21


@pytest.mark.asyncio
async def test_interrupted_dispatch_is_not_replayed(tmp_path: Path) -> None:
    database = Database(tmp_path / "recover.db")
    await database.initialize()
    timer = await database.create_timer(
        TimerCreate(
            device_id="salon",
            action=TimerAction.OFF,
            execute_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    claimed = await database.claim_due_timer()
    assert claimed and claimed.status == TimerStatus.RUNNING
    assert await database.recover_interrupted() == 1
    recovered = await database.get_timer(timer.id)
    assert recovered and recovered.status == TimerStatus.UNKNOWN
    assert "not retried" in (recovered.error or "")


@pytest.mark.asyncio
async def test_timer_idempotency_key_is_unique(tmp_path: Path) -> None:
    database = Database(tmp_path / "idempotent.db")
    await database.initialize()
    data = TimerCreate(
        device_id="salon",
        action=TimerAction.OFF,
        execute_at=datetime.now(UTC) + timedelta(minutes=1),
        idempotency_key="same-request",
    )
    await database.create_timer(data)
    with pytest.raises(ValueError, match="already exists"):
        await database.create_timer(data)
