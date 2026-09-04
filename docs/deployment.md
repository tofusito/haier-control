# Deployment

## Persistent layout

The repository, runtime data, and secrets must be siblings but separate:

```text
<runtime-root>/
  repository/       # clean Git checkout; public source of truth
  data/             # /data: SQLite, audit, timers, encrypted hOn session
  secrets/          # master_key and optional hon_email/hon_password, mode 0600
```

The application image is built as `haier-control:<12-char-git-sha>`. The Compose service
uses the fixed local alias `haier-control:deployed`; the deployment script moves that
alias only after a successful build and retains the revision tag. It never uses `latest`.

## Safe update script

Run `scripts/deploy-homelab.sh <absolute-stack-directory>` from the server checkout. The
script:

1. takes a read-only inventory of every container in the Compose project;
2. rejects a dirty checkout and uses `git pull --ff-only`;
3. checks container name, port, data directory, and network mode;
4. builds the immutable revision tag and points `haier-control:deployed` at it;
5. validates Compose without printing expanded configuration;
6. recreates only `haier-control` with `--no-deps --force-recreate`;
7. waits for health, checks the app endpoint, verifies Home Assistant and every preexisting
   stack container kept the same ID and a running/healthy state;
8. on any failure, restores the prior image ID and recreates or removes only
   `haier-control`.

The initial insertion of the service into a root-owned DockerHand stack remains a separate,
explicitly reviewed action. Do not run the deploy script before the Compose fragment,
volume, and secret exist.

For recommended automatic login, create `secrets/hon_email` and `secrets/hon_password`
interactively on the host with mode `0600`, then add the two `_FILE` variables and
read-only bind mounts from `deploy/homelab-service.template.yaml`. Never paste their values
into DockerHand. Remove those four Compose lines to return to manual pairing; the encrypted
session in `/data` remains reusable until hOn requires reauthentication.
