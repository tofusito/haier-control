# Configuration

Application settings use the `HAIER_` prefix and are defined in
[`Settings`](../app/settings.py). Keep credentials and runtime data outside Git.

## Runtime settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAIER_DRIVER` | `mock` | Select `mock` or `haier-cloud`. |
| `HAIER_DATABASE_PATH` | `/data/haier-control.db` | SQLite database file. |
| `HAIER_ENCRYPTED_SESSION_FILE` | `/data/haier-session.enc` | Encrypted hOn session; related encrypted recovery files live beside it. |
| `HAIER_MASTER_KEY_FILE` | `/run/secrets/haier_control_master_key` | Separate encryption and token-verification key; at least 32 bytes. |
| `HAIER_BOOTSTRAP_TOKEN_FILE` | `/run/secrets/haier_control_bootstrap_token` | Optional private bootstrap secret. |
| `HAIER_BIND_HOST` | `127.0.0.1` | Native server bind address. Docker binds internally to `0.0.0.0`. |
| `HAIER_PORT` | `8787` | Native server port. |
| `HAIER_LOG_LEVEL` | `INFO` | Application log level. |
| `HAIER_COMMAND_DEDUPE_SECONDS` | `3` | Duplicate-command window, 1–30 seconds. |
| `HAIER_POLL_INTERVAL_SECONDS` | `30` | Reserved setting (10–600); no continuous background poll currently uses it. |
| `HAIER_HAIER_CLIENT_ID` | Bundled interoperability client ID | Advanced authentication override; normally leave unchanged. |

There is no `HAIER_DATA_DIR` setting. Set the individual file paths when running natively.
Docker mounts `./data` at `/data`.

The Compose file additionally reads `HAIER_CONTROL_BIND_ADDRESS` (default `127.0.0.1`),
`HAIER_CONTROL_PORT` (default `8787`) and `HAIER_CONTROL_IMAGE`
(default `haier-control:dev`). Other native settings must be explicitly added to the
Compose service's `environment` block to reach the container.

## Connecting to hOn

Create the container with the cloud driver:

```sh
HAIER_DRIVER=haier-cloud docker compose up --build -d
```

Keep the driver selection in your private deployment environment for later updates.
The initial container log prints a pairing token valid for ten minutes. Open the dashboard,
choose **Connect to hOn** and enter the pairing token and your hOn credentials. If
requested, enter the email verification code. This is Haier Control's own form.

Interactive pairing keeps the password only during sign-in and saves the encrypted session.
For terminal pairing instead, run:

```sh
docker compose exec haier-control haier-control auth
```

Use web pairing only over a trusted connection; HTTP does not encrypt credentials.

## Optional automatic recovery

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAIER_HON_EMAIL_FILE` | Unset | Private hOn email file. |
| `HAIER_HON_PASSWORD_FILE` | Unset | Private hOn password file. |
| `HAIER_HON_EMAIL` | Unset | Convenience email input; visible in container metadata. |
| `HAIER_HON_PASSWORD` | Unset | Convenience password input; visible in container metadata. |

Prefer the file pair. Both host files must be regular files with mode `0600`, mounted
read-only. See the commented mounts in [compose.yaml](../compose.yaml). A complete file pair
takes precedence over direct environment values.

Automatic recovery imports credentials into an encrypted recovery file. Removing the
original environment inputs alone does not remove this encrypted copy. See
[deployment and recovery](deployment.md) for deliberate removal and backups.
Network failures do not trigger password sign-in; a failed recovery backs off for five minutes.
Vendor MFA can still require manual input.

## Trusted home-network browser access

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAIER_TRUSTED_NETWORK_MODE` | `false` | Enable signed browser sessions for selected source networks. |
| `HAIER_TRUSTED_NETWORK_CIDRS` | Empty | Explicit comma-separated source CIDRs. |
| `HAIER_TRUSTED_NETWORK_CONFIG_FILE` | `/data/haier-trusted-network.conf` | Optional host-managed mode/CIDR file. |
| `HAIER_TRUSTED_SESSION_TTL_SECONDS` | `2592000` | Signed-session lifetime, 300–31536000 seconds. |

Trusted access requires both an allowed direct source IP and a signed `HttpOnly` cookie.
Forwarded IP headers do not grant access. Empty networks and default routes are rejected.
See the [security model](security.md) before enabling this option.

## Persistent state

Back up the SQLite database using its backup API, preserve the encrypted session/recovery
files and keep the master key separately. Losing the key invalidates encrypted data and
local API token verification. Cloud access is still needed to rebuild live device state.
