# Architecture

## Runtime boundaries

The application is one process and one container. FastAPI serves the static UI and the
versioned API. SQLite in `/data` stores:

- API token hashes and scopes;
- persistent timers and execution state;
- a redacted command audit trail;
- non-secret local configuration.

The encrypted hOn session is also stored in `/data/haier-session.enc`. The encryption key
is supplied independently at `/run/secrets/haier_control_master_key`; it must never live in
the volume or Git.

The hOn password is never written to `/data`. Access, ID, Cognito, and refresh tokens are
encrypted. On startup a reusable encrypted session takes precedence; otherwise an
optional pair of private files wins over optional direct environment credentials. Each
automatic source is attempted once and references are cleared afterward. Failure falls
back to **Reconectar con hOn**; an MFA challenge pauses in memory and the UI requests only
the OTP. Existing timers remain in SQLite and are not executed while control is
unavailable; failure is visible.

## Drivers

`app.drivers.base.Driver` is the internal compatibility boundary:

- `list_devices()` returns capabilities advertised by each unit;
- `get_state()` returns nullable, timestamped state and never invents missing values;
- `send_command()` returns only after the backend explicitly accepts the command.

The UI is capability-based. A control is absent if the current model does not advertise
it. Known hOn numeric values are translated only after they appear in the model's live
schema.

## Timers and crash semantics

Timer rows are claimed with `BEGIN IMMEDIATE` before dispatch. A normal container restart
before the due time is safe: the scheduler resumes from SQLite. A crash in the tiny window
after dispatch but before confirmation cannot be made exactly-once because hOn offers no
documented idempotency key. The timer is therefore marked `unknown` on restart and is not
automatically replayed; this avoids sending the same physical command twice.

Each timer has a local idempotency key, audit history, execution timestamp, and explicit
`scheduled`, `running`, `executed`, `failed`, `cancelled`, or `unknown` state.

## Realtime

SSE carries local device/timer changes to open browsers. REST polling is the planned
authoritative reconciliation path for the cloud driver. AWS IoT MQTT is intentionally
not enabled in v0.1; adding it belongs inside `HaierCloudDriver`, not in the API or UI.
