# Security model

## Trust boundary

The default bind address is loopback. A homelab deployment may bind one selected LAN
address and port, but must not publish or tunnel it to the internet. No API route except
`/healthz` is anonymously usable. The HTML shell is public but contains no device data.

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
- Only encrypted token material is persisted. The password is requested again only when
  refresh fails and full reauthentication is required.

## Local API

Tokens contain at least 256 bits of randomness and are shown once. SQLite stores an
HMAC-SHA-256 digest keyed by the master secret, never the token. Scopes are `read`,
`control`, and `timers`. Rate limits are intentionally conservative and command
fingerprints suppress rapid duplicate physical actions.

## Threat model

Encryption at rest protects copied `/data` without the separate secret. It does not
protect against a root compromise, a running-container compromise, malicious host
administration, or a browser/device that already holds an API token. SQLite and token
files are mode `0600`; the container drops capabilities, uses a read-only root filesystem,
and receives only the one secret it needs.
