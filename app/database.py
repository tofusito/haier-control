from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import TimerAction, TimerCreate, TimerStatus, TimerUpdate, TimerView


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    scopes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS timers (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('on', 'off')),
                    execute_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    execution_started_at TEXT,
                    executed_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS timers_due_idx
                    ON timers(status, execute_at);
                CREATE TABLE IF NOT EXISTS command_audit (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    outcome TEXT NOT NULL,
                    source TEXT NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS command_recent_idx
                    ON command_audit(device_id, fingerprint, requested_at);
                """
            )
            await db.commit()
        self.path.chmod(0o600)

    async def ping(self) -> bool:
        try:
            async with aiosqlite.connect(self.path) as db:
                row = await (await db.execute("SELECT 1")).fetchone()
                return row == (1,)
        except aiosqlite.Error:
            return False

    async def token_count(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute("SELECT COUNT(*) FROM api_tokens WHERE revoked_at IS NULL")
            ).fetchone()
            return int(row[0]) if row else 0

    async def create_token(self, name: str, digest: str, scopes: set[str]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    str(uuid.uuid4()),
                    name,
                    digest,
                    json.dumps(sorted(scopes)),
                    iso(utc_now()),
                ),
            )
            await db.commit()

    async def authenticate_token(self, digest: str) -> set[str] | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT id, scopes_json FROM api_tokens "
                    "WHERE token_hash = ? AND revoked_at IS NULL",
                    (digest,),
                )
            ).fetchone()
            if not row:
                return None
            await db.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (iso(utc_now()), row[0]),
            )
            await db.commit()
            return set(json.loads(row[1]))

    async def create_timer(self, data: TimerCreate) -> TimerView:
        timer_id = str(uuid.uuid4())
        now = utc_now()
        key = data.idempotency_key or str(uuid.uuid4())
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute(
                    """
                    INSERT INTO timers (
                        id, device_id, action, execute_at, status, command_json,
                        idempotency_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timer_id,
                        data.device_id,
                        data.action.value,
                        iso(data.execute_at),
                        TimerStatus.SCHEDULED.value,
                        json.dumps(data.command, separators=(",", ":"), sort_keys=True),
                        key,
                        iso(now),
                        iso(now),
                    ),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("idempotency_key already exists") from exc
        result = await self.get_timer(timer_id)
        assert result is not None
        return result

    async def list_timers(self, *, include_finished: bool = True) -> list[TimerView]:
        where = "" if include_finished else "WHERE status IN ('scheduled', 'running')"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(f"SELECT * FROM timers {where} ORDER BY execute_at")  # noqa: S608
            ).fetchall()
        return [self._timer(row) for row in rows]

    async def get_timer(self, timer_id: str) -> TimerView | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute("SELECT * FROM timers WHERE id = ?", (timer_id,))
            ).fetchone()
        return self._timer(row) if row else None

    async def update_timer(self, timer_id: str, update: TimerUpdate) -> TimerView | None:
        current = await self.get_timer(timer_id)
        if not current or current.status != TimerStatus.SCHEDULED:
            return None
        execute_at = update.execute_at or current.execute_at
        command = current.command if update.command is None else update.command
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE timers SET execute_at = ?, command_json = ?, updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (iso(execute_at), json.dumps(command), iso(utc_now()), timer_id),
            )
            await db.commit()
        return await self.get_timer(timer_id)

    async def cancel_timer(self, timer_id: str) -> TimerView | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE timers SET status = 'cancelled', updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (iso(utc_now()), timer_id),
            )
            await db.commit()
            if cursor.rowcount != 1:
                return None
        return await self.get_timer(timer_id)

    async def claim_due_timer(self) -> TimerView | None:
        now = iso(utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            row = await (
                await db.execute(
                    "SELECT * FROM timers WHERE status = 'scheduled' AND execute_at <= ? "
                    "ORDER BY execute_at LIMIT 1",
                    (now,),
                )
            ).fetchone()
            if not row:
                await db.rollback()
                return None
            changed = await db.execute(
                "UPDATE timers SET status = 'running', execution_started_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (now, now, row["id"]),
            )
            await db.commit()
            if changed.rowcount != 1:
                return None
        return await self.get_timer(str(row["id"]))

    async def recover_interrupted(self) -> int:
        now = iso(utc_now())
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE timers SET status = 'unknown', updated_at = ?, "
                "error = 'Execution was interrupted; not retried to avoid a duplicate command' "
                "WHERE status = 'running'",
                (now,),
            )
            await db.commit()
            return cursor.rowcount

    async def finish_timer(self, timer_id: str, *, success: bool, error: str | None) -> None:
        now = iso(utc_now())
        status = TimerStatus.EXECUTED.value if success else TimerStatus.FAILED.value
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE timers SET status = ?, executed_at = ?, updated_at = ?, error = ? "
                "WHERE id = ? AND status = 'running'",
                (status, now, now, error, timer_id),
            )
            await db.commit()

    async def command_seen_recently(
        self, device_id: str, fingerprint: str, since: datetime
    ) -> bool:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT 1 FROM command_audit WHERE device_id = ? AND fingerprint = ? "
                    "AND requested_at >= ? AND outcome IN ('pending', 'success') LIMIT 1",
                    (device_id, fingerprint, iso(since)),
                )
            ).fetchone()
            return row is not None

    async def audit_start(
        self, device_id: str, operation: str, fingerprint: str, source: str
    ) -> str:
        audit_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO command_audit VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?, NULL)",
                (audit_id, device_id, operation, fingerprint, iso(utc_now()), source),
            )
            await db.commit()
        return audit_id

    async def audit_finish(self, audit_id: str, success: bool, detail: str | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE command_audit SET completed_at = ?, outcome = ?, detail = ? WHERE id = ?",
                (iso(utc_now()), "success" if success else "failed", detail, audit_id),
            )
            await db.commit()

    @staticmethod
    def _timer(row: Any) -> TimerView:
        return TimerView(
            id=row["id"],
            device_id=row["device_id"],
            action=TimerAction(row["action"]),
            execute_at=parse_datetime(row["execute_at"]),
            status=TimerStatus(row["status"]),
            command=json.loads(row["command_json"]),
            idempotency_key=row["idempotency_key"],
            created_at=parse_datetime(row["created_at"]),
            updated_at=parse_datetime(row["updated_at"]),
            executed_at=parse_datetime(row["executed_at"]),
            error=row["error"],
        )
