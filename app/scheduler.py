from __future__ import annotations

import asyncio
import contextlib

from app.controller import Controller
from app.database import Database
from app.events import EventBus
from app.models import CommandRequest, TimerAction, TimerView


class TimerScheduler:
    def __init__(self, database: Database, controller: Controller, events: EventBus) -> None:
        self.database = database
        self.controller = controller
        self.events = events
        self._task: asyncio.Task[None] | None = None
        self.healthy = False

    async def start(self) -> None:
        await self.database.recover_interrupted()
        self.healthy = True
        self._task = asyncio.create_task(self._run(), name="persistent-timer-scheduler")

    async def stop(self) -> None:
        self.healthy = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            timer = await self.database.claim_due_timer()
            if timer:
                await self._execute(timer)
                continue
            await asyncio.sleep(1)

    async def _execute(self, timer: TimerView) -> None:
        try:
            if timer.action == TimerAction.OFF:
                await self.controller.command(
                    timer.device_id,
                    CommandRequest(operation="power", value=False),
                    source=f"timer:{timer.id}",
                )
            else:
                await self.controller.command(
                    timer.device_id,
                    CommandRequest(operation="power", value=True),
                    source=f"timer:{timer.id}",
                )
                await self._apply_on_options(timer)
            await self.database.finish_timer(timer.id, success=True, error=None)
        except Exception as exc:
            await self.database.finish_timer(
                timer.id, success=False, error=f"{type(exc).__name__}: command not confirmed"
            )
        updated = await self.database.get_timer(timer.id)
        if updated:
            await self.events.publish("timer", updated.model_dump(mode="json"))

    async def _apply_on_options(self, timer: TimerView) -> None:
        options = timer.command
        sequence: list[CommandRequest] = []
        if "mode" in options:
            sequence.append(CommandRequest(operation="set_mode", value=options["mode"]))
        if "temperature" in options:
            sequence.append(
                CommandRequest(operation="set_temperature", value=options["temperature"])
            )
        if "fan_mode" in options:
            sequence.append(CommandRequest(operation="set_fan", value=options["fan_mode"]))
        if "vertical_swing" in options:
            sequence.append(
                CommandRequest(operation="set_vertical_swing", value=options["vertical_swing"])
            )
        if "horizontal_swing" in options:
            sequence.append(
                CommandRequest(operation="set_horizontal_swing", value=options["horizontal_swing"])
            )
        for key, value in options.get("advanced", {}).items():
            sequence.append(
                CommandRequest(operation="set_advanced", key=str(key), value=value)
            )
        for command in sequence:
            await self.controller.command(
                timer.device_id, command, source=f"timer:{timer.id}"
            )
