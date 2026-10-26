# Troubleshooting

## The demo cannot import the app

Run commands from the repository root with the virtual environment activated. Install
the project using `pip install -e .`, then run `python -m scripts.demo`.

## The port is already in use

Choose another local port: `python -m scripts.demo --port 8789`. For Docker, set
`HAIER_CONTROL_PORT` before creating the service. Keep the normal demo on loopback.

## A fresh Docker installation cannot read the database or key

The image runs as UID/GID 1000. Ensure the mounted data directory is writable and the
master key is readable by that container user. Permissions `0700`/`0600` work only when
ownership also matches. Correct ownership for the deployment host; do not make secrets
world-readable. See [deployment](deployment.md).

## The dashboard asks for an API token

This is normal in protected mode. Generate a token with
`docker compose exec haier-control haier-control token --name browser` and keep it private.
Use a local API token, not an hOn password. Another browser needs its own access setup
unless trusted-network sessions have been explicitly configured.

## hOn sign-in fails or asks for a code

Check credentials in the official app first. A pairing token is short-lived and consumed
by an attempt; restart only your Haier Control container to obtain a fresh initial token
when manual pairing requires one. MFA codes belong in the verification step. Avoid repeated
login attempts; hOn may rate-limit or change the authentication flow.

## No devices or some controls are missing

The mock driver reports two simulated rooms. The cloud driver supports AC devices and
settings-based command schemas. An unadvertised capability stays hidden. A changed or
unexpected cloud response is an error, not a successful empty result. See
[compatibility](compatibility.md) before filing a redacted bug report.

## The physical remote changed the unit, but the dashboard did not update

Click the connection-status button to refresh. The app currently reads cloud state on
request. Local SSE covers changes processed by the app; there is no MQTT connection or
continuous background state poll.

## A timer did not execute

Check that the application server was running and hOn was reachable. Timer records can
be `failed` or `unknown`, not only `executed`. An interrupted dispatch is deliberately
not replayed automatically because the physical action may already have happened.
See [timer crash semantics](architecture.md#timers-and-crash-semantics).

When asking for help, include the commit, deployment method and a redacted error. Remove
credentials, tokens, account identifiers and geographic coordinates before sharing logs.
