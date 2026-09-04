#!/usr/bin/env bash

set -euo pipefail

readonly STACK_DIR="${1:?usage: deploy-homelab.sh ABSOLUTE_STACK_DIR [BRANCH]}"
readonly BRANCH="${2:-main}"
readonly REPOSITORY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly RUNTIME_ROOT="$(cd "${REPOSITORY_DIR}/.." && pwd)"
readonly DATA_DIR="${RUNTIME_ROOT}/data"
readonly SECRET_FILE="${RUNTIME_ROOT}/secrets/master_key"
readonly COMPOSE_FILE="${STACK_DIR}/compose.yaml"
readonly SERVICE="haier-control"
readonly DEPLOY_ALIAS="haier-control:deployed"
readonly LOCK_FILE="${RUNTIME_ROOT}/deploy.lock"
readonly INVENTORY_BEFORE="$(mktemp)"
readonly INVENTORY_AFTER="$(mktemp)"

cleanup() { rm -f "${INVENTORY_BEFORE}" "${INVENTORY_AFTER}"; }
trap cleanup EXIT

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another Haier Control deployment is already running." >&2
  exit 1
fi

compose() {
  docker compose --project-directory "${STACK_DIR}" -f "${COMPOSE_FILE}" "$@"
}

inventory() {
  local id
  while read -r id; do
    [[ -z "${id}" ]] && continue
    docker inspect --format \
      '{{.Name}}|{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "${id}"
  done < <(docker ps -aq --filter label=com.docker.compose.project=homeassistant) | sort
}

wait_for_health() {
  local attempt state
  for attempt in $(seq 1 60); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${SERVICE}" 2>/dev/null || true)"
    [[ "${state}" == "healthy" ]] && return 0
    [[ "${state}" == "exited" || "${state}" == "dead" ]] && return 1
    sleep 2
  done
  return 1
}

verify_existing_unchanged() {
  grep -v '^/haier-control|' "${INVENTORY_BEFORE}" >"${INVENTORY_BEFORE}.existing"
  grep -v '^/haier-control|' "${INVENTORY_AFTER}" >"${INVENTORY_AFTER}.existing"
  diff -u "${INVENTORY_BEFORE}.existing" "${INVENTORY_AFTER}.existing" >/dev/null
  while IFS='|' read -r name _id _image status health; do
    [[ -z "${name}" ]] && continue
    if [[ "${status}" != "running" || "${health}" == "unhealthy" ]]; then
      echo "Existing service is not running after deployment: ${name}" >&2
      return 1
    fi
  done <"${INVENTORY_AFTER}.existing"
}

rollback() {
  echo "Deployment failed; reverting Haier Control only." >&2
  if [[ -n "${PREVIOUS_IMAGE_ID:-}" ]]; then
    docker tag "${PREVIOUS_IMAGE_ID}" "${DEPLOY_ALIAS}"
    compose up -d --no-deps --force-recreate "${SERVICE}" || true
    wait_for_health || true
  elif docker ps -a --format '{{.Names}}' | grep -qx "${SERVICE}"; then
    compose rm -sf "${SERVICE}" || true
  fi
}

[[ "${STACK_DIR}" == /* ]] || { echo "Stack directory must be absolute." >&2; exit 1; }
[[ -f "${COMPOSE_FILE}" ]] || { echo "Compose file not found." >&2; exit 1; }
[[ -d "${DATA_DIR}" && ! -L "${DATA_DIR}" ]] || { echo "Dedicated data directory is missing or unsafe." >&2; exit 1; }
[[ -r "${SECRET_FILE}" ]] || { echo "Master-key secret is missing or unreadable." >&2; exit 1; }
[[ -z "$(git -C "${REPOSITORY_DIR}" status --porcelain)" ]] || { echo "Dirty checkout; refusing deployment." >&2; exit 1; }

inventory >"${INVENTORY_BEFORE}"
if docker ps -a --filter name='^/haier-control$' --format '{{.Label "com.docker.compose.project"}}' | grep -qv '^homeassistant$'; then
  echo "Container-name collision outside the target stack." >&2
  exit 1
fi
if ! grep -q '^  haier-control:$' "${COMPOSE_FILE}"; then
  echo "The reviewed haier-control service is not present in the target Compose." >&2
  exit 1
fi
if ! grep -q 'network_mode: bridge' "${COMPOSE_FILE}"; then
  echo "Unexpected network mode for haier-control." >&2
  exit 1
fi
if ! docker ps -a --format '{{.Names}}' | grep -qx "${SERVICE}" \
  && ss -H -ltn 'sport = :8787' | grep -q .; then
  echo "TCP port 8787 is already in use." >&2
  exit 1
fi

# Parse the expanded model internally and emit no configuration or secret values.
if ! compose config --format json | python3 -c '
import json, sys
service = json.load(sys.stdin).get("services", {}).get("haier-control", {})
assert service.get("container_name") == "haier-control"
assert service.get("network_mode") == "bridge"
mounts = service.get("volumes", [])
assert any(item.get("target") == "/data" for item in mounts if isinstance(item, dict))
assert any(item.get("target") == "/run/secrets/haier_control_master_key" for item in mounts if isinstance(item, dict))
'; then
  echo "Compose service shape failed the safe preflight." >&2
  exit 1
fi

git -C "${REPOSITORY_DIR}" pull --ff-only origin "${BRANCH}"
readonly REVISION="$(git -C "${REPOSITORY_DIR}" rev-parse HEAD)"
readonly SHORT_REVISION="$(git -C "${REPOSITORY_DIR}" rev-parse --short=12 HEAD)"
readonly IMAGE="haier-control:${SHORT_REVISION}"
PREVIOUS_IMAGE_ID="$(docker image inspect "${DEPLOY_ALIAS}" --format '{{.Id}}' 2>/dev/null || true)"

docker build --pull \
  --label "org.opencontainers.image.revision=${REVISION}" \
  --tag "${IMAGE}" "${REPOSITORY_DIR}"
docker tag "${IMAGE}" "${DEPLOY_ALIAS}"

if ! compose config --quiet; then
  rollback
  exit 1
fi

if ! compose up -d --no-deps --force-recreate "${SERVICE}" || ! wait_for_health; then
  rollback
  exit 1
fi

if ! docker exec "${SERVICE}" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).read()"; then
  rollback
  exit 1
fi

inventory >"${INVENTORY_AFTER}"
if ! verify_existing_unchanged; then
  rollback
  exit 1
fi

if ! docker exec homeassistant python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8123/', timeout=5).read(1)"; then
  rollback
  exit 1
fi

echo "Haier Control is healthy on ${IMAGE} (${REVISION})."
