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

Access, ID, Cognito, and refresh tokens are encrypted and reused across restarts. Automatic
credentials (private files first, environment second) update `haier-credentials.enc` at
startup. Without either input, the saved encrypted credentials remain available for
recovery. Successful refresh rotations are persisted atomically; concurrent renewals share
a lock. A rejected refresh falls back to full login; failures back off for five minutes.
Network outages never trigger a password login. MFA may require manual verification.

`haier-devices.enc` holds stable local IDs, vendor identifiers, names, models, capabilities
and command schemas. Discovery reconciles hourly and after restart, preserving the last
inventory if the cloud is unavailable. An authoritative empty result removes old devices.
State and command execution still use the cloud; a cached inventory is not offline control.

`browser-setup.enc` preserves the first local API token until an authenticated browser
acknowledges it after saving to localStorage. Routine status reads and container restarts
cannot consume this delivery. Existing SQLite token hashes remain compatible.

An explicit trusted home-network deployment can instead issue a signed `HttpOnly` browser
session cookie after checking the direct TCP peer against configured Wi-Fi/Tailscale CIDRs.
The cookie grants the normal local scopes without exposing an API token, hOn credentials,
or the master key to JavaScript. Bearer tokens remain supported for integrations; proxy
headers are never used to decide trust.

## UI consistency and latency

The web client applies each control locally before waiting for hOn: power, temperature,
mode, fan, swing, and advanced settings immediately update the card, icon, color, label,
and accessibility state. The card remains visibly active while a small synchronization
indicator and `aria-busy` state prevent duplicate taps. Requests are aborted after ten
seconds; a rejection or timeout restores the last known state, surfaces a short retry
message, and schedules a fresh read.

The cloud driver reuses a context response for up to eight seconds, invalidates it after an
accepted command, and does not perform a redundant second state request before returning.
The UI keeps the accepted optimistic value for a short reconciliation window so an older
SSE/REST response cannot immediately overwrite it. Once hOn reports the requested value,
the remote state replaces the local snapshot silently; if it does not converge, the next
fresh read wins. Timer create, edit, and cancel operations use the same local-first pattern
and keep pending mutations ahead of stale SSE timer events.

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
