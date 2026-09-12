# Configuration

The container is configured with environment variables. Keep credentials and the master
key outside Git.

## Core settings

| Variable | Purpose | Default |
| --- | --- | --- |
| `HAIER_DRIVER` | `mock` or `haier-cloud` | `mock` |
| `HAIER_MASTER_KEY_FILE` | Path to the encryption key | `/run/secrets/master_key` |
| `HAIER_DATA_DIR` | Persistent SQLite and session data | `/data` |
| `HAIER_HON_EMAIL_FILE` | hOn email secret file | unset |
| `HAIER_HON_PASSWORD_FILE` | hOn password secret file | unset |
| `HAIER_TRUSTED_NETWORK_MODE` | Enable LAN/tailnet browser sessions | disabled |
| `HAIER_TRUSTED_NETWORK_CIDRS` | Allowed source CIDRs | unset |

Prefer `*_FILE` settings for credentials. The host files should be regular files with
mode `0600`, mounted read-only, and excluded from backups shared with other systems.

## Persistent state

Back up `/data` and the master key separately. Losing the master key makes encrypted
session recovery unreadable. Deleting the session data forces a new hOn pairing but does
not affect the appliance account.

## Network boundary

Run the service on a trusted LAN or tailnet. Trusted-network mode requires both a source
IP in the configured CIDRs and a signed browser cookie. It is not an internet access
control and must not be enabled behind an untrusted public proxy.
