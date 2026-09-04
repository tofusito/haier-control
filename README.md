# Haier Control

Haier Control is a small, local-first web application for two or more Haier hOn air
conditioners. It provides a pleasant mobile UI, an authenticated API, and persistent
on/off timers without depending on Home Assistant at runtime.

> [!WARNING]
> The hOn cloud API is private and unofficial. It can change without notice. This project
> never treats a malformed response or an unconfirmed command as success.

## What works in v0.1

- Responsive mobile dashboard with mode-specific visual language, target and room
  temperature, fan, swing, advanced controls, reduced-motion support, and honest stale or
  degraded states.
- Persistent timers to turn a unit on or off after N minutes or at an exact date/time.
  Turn-on timers may include mode, target temperature, fan, swing, and advanced options.
- Stable versioned API for devices, commands, timers, SSE updates, and authenticated
  OpenAPI at `/api/v1/openapi.json`.
- API tokens are random and stored only as keyed hashes, with `read`, `control`, and
  `timers` scopes. Commands are rate-limited, deduplicated, and audited.
- `MockDriver` is fully functional. `HaierCloudDriver` is opt-in and uses REST polling;
  MQTT realtime is a documented future enhancement.
- hOn login through encrypted-session reuse, private credential files, direct environment
  variables, CLI, or a short-lived web pairing flow, including Salesforce email OTP.

## Architecture

The domain and API depend only on a `Driver` contract. `MockDriver` and
`HaierCloudDriver` sit behind it, so future Home Assistant, ESPHome/local, or other
connectors do not change the UI, timers, or local API.

```text
mobile web / Hermes
        |
authenticated API + SSE
        |
controller -- persistent scheduler -- SQLite /data
        |
Driver interface
   |             |
MockDriver   HaierCloudDriver -> private hOn cloud
```

See [architecture](docs/architecture.md), [protocol notes](docs/haier-protocol.md),
[security](docs/security.md), and [deployment](docs/deployment.md).

## Local quick start (mock)

```sh
mkdir -p data secrets
chmod 700 data secrets
python -c 'import secrets; print(secrets.token_urlsafe(48))' > secrets/master_key
chmod 600 secrets/master_key
docker compose up --build -d
docker compose exec haier-control haier-control token --name browser
```

Open `http://127.0.0.1:8787` and paste the token printed once by the CLI.

To try direct hOn access, set `HAIER_DRIVER=haier-cloud` before creating the container.
The container prints a one-use pairing token valid for ten minutes. Open the web UI,
choose **Conectar con hOn**, and enter the pairing token plus hOn credentials. If email
OTP is enabled, the same flow prompts for the code. This form belongs to Haier Control;
it is not an embedded official page.

The web setup over HTTP should only be used on a trusted LAN. For stronger protection,
bootstrap inside the container instead:

```sh
docker compose exec haier-control haier-control auth
```

### Optional automatic hOn login

Automatic login runs once at startup only when no encrypted hOn session can be reused.
Precedence is: encrypted session, private files, direct environment variables, then the
interactive UI. A successful login persists only encrypted session tokens. A failure
falls back to manual pairing without retrying in a loop; an email MFA challenge opens the
UI directly at the OTP step.

Recommended file mode:

```yaml
environment:
  HAIER_HON_EMAIL_FILE: /run/secrets/haier_hon_email
  HAIER_HON_PASSWORD_FILE: /run/secrets/haier_hon_password
volumes:
  - ./secrets/hon_email:/run/secrets/haier_hon_email:ro
  - ./secrets/hon_password:/run/secrets/haier_hon_password:ro
```

Both host files must be regular files with mode `0600`. Keep them outside Git and `/data`.
The process reads them once, removes its in-memory references after the attempt, and never
logs their contents.

For a hobby installation that accepts a weaker host-side boundary, set both
`HAIER_HON_EMAIL` and `HAIER_HON_PASSWORD`. Docker inspect, DockerHand, Compose output, and
host administrators can read direct environment values in clear text. Haier Control never
prints them, but cannot hide them from Docker. Do not combine direct values with file mode;
the complete file pair wins.

## Development

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
mypy app
pytest
```

## License and provenance

Haier Control is MIT licensed. It does not copy code from addhOn. Wire-level
interoperability facts are independently implemented from addhOn's public protocol
specification and are recorded in [the protocol notes](docs/haier-protocol.md). addhOn
itself is AGPL-3.0.
