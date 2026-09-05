# Security model

## Trust boundary

The default bind address is loopback. A homelab deployment may bind one selected LAN
address and port, but must not publish or tunnel it to the internet. Health and initial
setup routes are public in the default protected mode; device, command and timer routes
require a bearer token. The HTML shell is public but contains no device data.

HTTP does not encrypt hOn credentials or local API tokens. The web pairing flow is an
explicit convenience for a trusted LAN; CLI bootstrap or local HTTPS is safer.

## hOn setup

- A cryptographically random pairing token is printed once to the container log, expires
  after ten minutes, and is consumed at the first login attempt.
- The OTP flow has a separate random flow ID and CSRF token, stays only in memory, expires
  after ten minutes, limits attempts/resends, and keeps Salesforce session cookies only
  until completion.
- Email, password, OTP, MAC, serials, and cloud tokens are never logged. API errors expose
  fixed classifications, not vendor bodies.
- Password fields use appropriate browser autocomplete hints but are cleared after each
  submission. Responses have `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
  and framing disabled.
- Session tokens are encrypted. Automatic mode also encrypts recovery credentials in
  `/data/haier-credentials.enc`, so an expired refresh token does not require reconfiguration.
  Interactive-only passwords and OTPs are not persisted.

Automatic authentication is opt-in. A reusable encrypted session always wins. With file
mode, both credential files must be regular `0600` files mounted read-only; they are read
at startup to update the encrypted recovery copy. Direct `HAIER_HON_EMAIL` and
`HAIER_HON_PASSWORD` variables are a convenience mode and remain visible in the container
configuration to Docker, DockerHand, and host administrators. File mode wins over direct
variables, and any incomplete or unsafe pair fails closed to the interactive UI without a
immediate retry loop. Recovery first refreshes the saved token, then uses saved credentials
only when refresh is rejected. Concurrent renewals share a lock; failed attempts back off
for five minutes. Network failures do not trigger a password login. An MFA challenge may
still require interactive verification; the app cannot bypass a vendor challenge.

## Local API

Tokens contain at least 256 bits of randomness. SQLite stores an
HMAC-SHA-256 digest keyed by the master secret, never the token. Scopes are `read`,
`control`, and `timers`. Rate limits are intentionally conservative and command
fingerprints suppress rapid duplicate physical actions.

The first browser token is temporarily recoverable in encrypted `browser-setup.enc`.
Setup status reads do not consume it. The browser saves it in localStorage before sending
an authenticated acknowledgment using that token; acknowledgment deletes the encrypted
delivery copy. Until acknowledgment, trusted-LAN clients can obtain this initial token
through the setup route. Existing token hashes and browser tokens are unchanged on upgrade.
Clearing browser storage or using another browser still requires a local API token.

### Trusted home-network mode

`HAIER_TRUSTED_NETWORK_MODE=true` is an explicit convenience switch for a private home
deployment. `HAIER_TRUSTED_NETWORK_CIDRS` must list every allowed source network, normally
the home Wi-Fi CIDR plus the Tailscale IPv4/IPv6 ranges used by the household. Empty or
invalid configuration fails closed at startup. The server never trusts `X-Forwarded-For`
or another proxy header; it checks the direct TCP peer address because the shipped Uvicorn
configuration has proxy headers disabled.

When enabled, `/` sets a short-lived signed `HttpOnly` cookie only for an allowed source.
The browser uses that cookie for the dashboard API; it never sees hOn credentials, the
master key, or a bearer API token. The root dashboard and hOn credential/OTP routes reject
untrusted sources. Existing bearer tokens continue to work for integrations, including
from a different network, while the trusted cookie grants only the normal `read`,
`control`, and `timers` scopes. Keep the service off public reverse proxies and tunnels;
if a proxy must exist, keep it outside the trusted CIDRs or retain protected mode.

For root-owned DockerHand Compose files, the opt-in can be stored as the `0600` marker
`/data/haier-trusted-network.conf` with `mode=trusted` and a comma-separated `cidrs=`
line. It contains no secret and can be removed to restore bearer-token browser access.

## Threat model

Encryption at rest protects copied `/data` without the separate secret. It does not
protect against a root compromise, a running-container compromise, malicious host
administration, or a browser/device that already holds an API token. SQLite and token
files are mode `0600`; the container drops capabilities, uses a read-only root filesystem,
and receives only the one secret it needs.

File credentials protect against accidental disclosure in Compose values and container
inspection; they do not protect against host root, the Docker daemon, or a compromised
running process. Direct environment credentials additionally remain visible in Docker
metadata. The setup delivery described above assumes the selected LAN is trusted.
