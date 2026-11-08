# GitHub Actions production deployment

Production: https://muse.aicodeme.xyz/admin

Repository: https://github.com/ccfwwm/muse2api

Pushes to `main` run application checks, build an amd64 Docker image on GitHub,
publish it to GHCR, and deploy its immutable digest to `rakserver`. The workflow
can also be run manually from the Actions page. Other branches cannot deploy.
Before building, the workflow merges upstream `main` into the fork. Merge
conflicts stop deployment without overwriting local changes. An hourly schedule
checks upstream and retries outstanding deployments; unchanged healthy deployments
are skipped. The fork revision and the upstream revision are embedded separately
in the image, so local deployment commits never create a false upstream alert.

The SSH key is dedicated to this application. Its authorized-key entry uses
`restrict,command="/opt/muse2api/deploy/ssh-dispatch.py"`; it cannot open a shell,
forward ports, or run arbitrary server commands. The dispatcher accepts only a
commit SHA, image digest, and registry username. GHCR authentication uses the
current job's short-lived token in a temporary Docker configuration, removed
after deployment. The SSH server's host key is pinned.

Repository secrets:

- `MUSE_DEPLOY_SSH_KEY`: dedicated private deployment key.
- `MUSE_DEPLOY_KNOWN_HOSTS`: verified SSH host-key record, including custom port.

Repository variables:

- `MUSE_DEPLOY_HOST`: production server address.
- `MUSE_DEPLOY_PORT`: production SSH port.

The root-owned deployment files are installed at `/opt/muse2api/deploy/`.
Changes to these server control files require an explicit administrative
installation; publishing an application image does not overwrite them.

## Persistent state and access

`/opt/muse2api/state` is mounted as `/state`. Its `.env` contains the management
key and public URL. The application loads it directly; key rotation atomically
updates this persistent file and survives container replacement. Account cookies,
task records and media are in `state/data`; Chromium profiles are in
`state/profiles`. None of this state is committed to GitHub or baked into images.
The service binds only `127.0.0.1:18610`; Nginx provides HTTPS and unbuffered SSE.
The `muse2api` container also joins the existing external network
`sub2api-deploy_sub2api-network`. Sub2api can use
`http://muse2api:18610/v1` as the upstream API base URL; Muse2api can reach
`http://sub2api:8080`. No database container is created: JSON storage remains
isolated in this application's state directory. The Compose project and container
are both named `muse2api`, distinct from the existing Sub2api resources.
Let's Encrypt renews through the server's existing certbot timer and reload hook.

The application signs in with an `m2a_` management key and has no username.
The same key authenticates API requests as `Authorization: Bearer <key>`.
Import your own Muse session from the browser extension before generating content.
An empty account pool is reported by `/readyz` as `no_account`.

## Verification and rollback

Deployment verifies the image's commit label before replacing the container,
waits for Docker health, checks authenticated management and model endpoints,
and confirms unauthenticated management requests receive 401. Failure restores
the previous image. GitHub also checks the public HTTPS health endpoint.
Container replacement briefly interrupts active connections; deploy when no
generation tasks are running.

Before replacement, small configuration/account/task backups are saved under
`/opt/muse2api/backups/<UTC timestamp>-<commit>/` with private permissions.
Media remains on the persistent volume. Successful release metadata is saved in
`/opt/muse2api/deployment.json`. Backups are retained until manually removed.
To roll back manually, restore a backup's `release.env` to `.release.env`, then run:

```bash
cd /opt/muse2api
docker compose --env-file .release.env -f deploy/compose.production.yml up -d --wait
```

## Online update button

With `MUSE2API_UPDATE_MODE=github-actions`, the authenticated update button writes
a request to `/state/updates/request.json`. The host's `muse2api-update.path`
starts `muse2api-update.service`, whose controller runs outside the container.
It merges upstream in a disposable checkout and pushes a commit using a dedicated
repository-scoped deploy key. GitHub then builds and deploys normally. A manual
update also permits retrying/rebuilding the current upstream revision.

The controller watches verified deployment metadata and GitHub run failures;
`/admin/update/status` reports queued, syncing, building, completed, or failed.
The UI waits for confirmed success before reloading and resumes polling after
refresh. Duplicate requests return 409. Conflicts/build failures stay visible.
The container receives neither the write key nor access to Docker or systemd.

Host setup additionally installs `update-controller.py` with the other root-owned
control files, installs `muse2api-update.path` and `.service` in `/etc/systemd/system`,
and enables the path unit. `/opt/muse2api/updater/github_ed25519` is registered as
a write-enabled deploy key on this repository only, and `updater/known_hosts`
contains GitHub's SSH host keys verified through its HTTPS API. The systemd unit
may write only its checkout and update queue; it cannot edit deployed configuration.

Native in-container source replacement is bypassed in managed mode. Repository
pushes from the old maintainer UI remain blocked at Nginx; the online upgrade
routes now pass through to the authenticated queue API.
