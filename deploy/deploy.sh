#!/usr/bin/env bash
# Installed as a root-owned file; invoked only through ssh-dispatch.py.
set -euo pipefail
umask 077

revision=${1:?commit SHA required}
digest=${2:?image digest required}
registry_user=${3:?registry user required}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$registry_user" =~ ^[A-Za-z0-9][A-Za-z0-9-]*(\[bot\])?$ ]]

cd /opt/muse2api
exec 9>.deploy.lock
flock -w 900 9
docker network inspect sub2api-deploy_sub2api-network >/dev/null
IFS= read -r registry_token
[[ -n "$registry_token" ]]
task_docker_config=$(mktemp -d)
export DOCKER_CONFIG="$task_docker_config"
trap 'rm -rf "$task_docker_config"' EXIT
printf '%s' "$registry_token" | docker login ghcr.io --username "$registry_user" --password-stdin >/dev/null
unset registry_token

image="ghcr.io/ccfwwm/muse2api@$digest"
docker pull "$image"
actual_revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")
[[ "$actual_revision" == "$revision" ]] || { echo 'Image revision does not match requested commit' >&2; exit 1; }

release_id="$(date -u +%Y%m%dT%H%M%SZ)-${revision:0:12}"
mkdir -p "backups/$release_id"
if [[ -f .release.env ]]; then
    cp .release.env "backups/$release_id/release.env"
fi
# Back up configuration and account/task records; media stays on the persistent volume.
cp state/.env "backups/$release_id/app.env"
for record in accounts.json tasks.json .installed_sha; do
    if [[ -f "state/data/$record" ]]; then
        cp "state/data/$record" "backups/$release_id/$record"
    fi
done

printf 'MUSE2API_IMAGE=%s\nMUSE2API_REVISION=%s\n' "$image" "$revision" > .release.env.next
mv .release.env.next .release.env

verify_release() {
    docker compose --env-file .release.env -f deploy/compose.production.yml up -d --wait --wait-timeout 150 || return 1
    docker exec -i muse2api python - <<'PY' || return 1
import json
import socket
import urllib.error
import urllib.request
from config import CFG

assert CFG.api_key and CFG.api_key.startswith("m2a_"), "Management key is missing"
base = "http://127.0.0.1:18610"
headers = {"Authorization": "Bearer " + CFG.api_key}
for path in ("/admin/status", "/v1/models"):
    with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=10) as response:
        result = json.load(response)
        if path == "/admin/status":
            assert result["base_url"] == "https://muse.aicodeme.xyz/v1"
        else:
            assert result["data"], "Model list is empty"
try:
    urllib.request.urlopen(base + "/admin/status", timeout=10)
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("Unauthenticated management request was accepted")
with socket.create_connection(("sub2api", 8080), timeout=10):
    pass
print("Authenticated management and model endpoints passed; anonymous access denied")
print("Shared Docker network can resolve and connect to sub2api:8080")
PY
}

if ! verify_release; then
    echo 'Release failed verification; restoring previous image' >&2
    if [[ -f "backups/$release_id/release.env" ]]; then
        cp "backups/$release_id/release.env" .release.env
        docker compose --env-file .release.env -f deploy/compose.production.yml up -d --wait --wait-timeout 150
    else
        docker compose --env-file .release.env -f deploy/compose.production.yml stop
        rm -f .release.env
    fi
    exit 1
fi

printf '%s\n' "$revision" > state/data/.installed_sha.next
mv state/data/.installed_sha.next state/data/.installed_sha
python3 - "$revision" "$image" "$release_id" <<'PY'
import datetime
import json
import os
import sys
with open("deployment.json.next", "w") as output:
    json.dump({"revision": sys.argv[1], "image": sys.argv[2], "backup": sys.argv[3],
               "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "url": "https://muse.aicodeme.xyz"}, output, indent=2)
os.replace("deployment.json.next", "deployment.json")
PY
echo "Deployment verified: $revision"
