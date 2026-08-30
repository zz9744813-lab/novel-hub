# Release controller: candidate/deploy atomicity (ops rework A)

## New flow

```text
candidate <sha>   fetch -> validate sha on main -> prepare release worktree
                  -> build ALL images under the ISOLATED compose project
                     novelforge-candidate (own image names, own throwaway
                     postgres/redis, own network; zero production contact)
                  -> one-off containers: alembic upgrade head (throwaway db)
                     -> production_pack validate
                     -> production_pack qualify (real gateway, evidence lands
                        in the throwaway db only)
                  -> record $SHARED/candidates/<sha>.envelope
                     (sha / created / passed) + raw JSON payloads
                  -> tear the candidate containers/volumes/network down
                  -> exit 0 only if every gate passed

deploy <sha>      fetch -> prepare -> config check
                  -> REQUIRE a passed, fresh candidate envelope bound to the
                     exact sha (refuses missing/failed/stale: exit 71)
                  -> require the candidate-built images to exist
                  -> backup via `compose exec` only (no service is ever
                     started or recreated; if postgres is down, abort)
                  -> migration as a ONE-OFF container (--rm --no-deps; nothing
                     running is recreated or restarted)
                  -> switch symlink -> compose up -> health
                  -> on unhealthy: switch back + up previous release
                     (per-release image tags make this a true code rollback)
                  -> install controller, record DEPLOYED_SHA

status            reports current sha/health plus per-service release
                  provenance (api/web/worker/postgres/redis) and
                  mixed_release=true when any running container came from a
                  different release than the current symlink.
```

## Key mechanisms

- `deploy/docker-compose.candidate.yml`: overrides the shared postgres/redis
  image tags, the production network name and the web host-port binding so a
  candidate build/gate can never mutate production state.
- `RELEASE_TAG` interpolation: api/worker/web images are tagged
  `novelforge-<service>:<sha>` via a generated per-release env file
  (`$SHARED/release-env/<sha>.env` = shared `.env` + `RELEASE_TAG`). Rollback
  re-ups the previous release with ITS tag -> previous code, not a retag.
- Candidate results: `$SHARED/candidates/<sha>.envelope` + `<sha>.raw.*/`
  JSON payloads (audit). `CANDIDATE_MAX_AGE_SECONDS` (default 86400) bounds
  freshness; deploy refuses anything older.
- Dead/pending outbox-style retries are not applicable here; a failed
  candidate is simply re-run by the operator after fixing the cause. Nothing
  is deleted: releases, backups and candidate evidence accumulate.

## Bootstrap sequencing (important)

The running `/usr/local/sbin/novelforge-release` is only replaced AFTER a
healthy switch (`install_release_controller`). Therefore the FIRST deploy
performed after this controller is merged still executes the OLD controller
logic (including its qualify-on-production and postgres recreation); that
deploy installs the new controller, and every deploy AFTER it gets the
atomic candidate->deploy flow, the `candidate` verb and mixed-release
detection. To activate the new controller without any production deploy,
a root console can run:

```bash
install -o root -g root -m 0755 \
  /srv/novelforge/releases/<sha>/deploy/ops/novelforge-release /usr/local/sbin/novelforge-release
install -o root -g root -m 0755 \
  /srv/novelforge/releases/<sha>/deploy/ops/novelforge-ops /usr/local/sbin/novelforge-ops
```

## Tests

`deploy/ops/tests/run_tests.sh` runs the real controller against stub
`docker`/`curl`/`install`/`flock` binaries and a real git mirror. It covers:
candidate auto-prepare + isolated gates; model-gate failure leaving
production untouched (zero prod compose calls); deploy refusing missing /
failed / stale candidates; the happy path (backup, one-off migration,
switch, health, no image rebuild); migration failure aborting before
switch; health failure rolling symlink + services back; status detecting
mixed and consistent provenance; missing candidate images refusing before
backup.

Run inside a real Linux environment (WSL on this workstation):

```bash
wsl -e bash -c "cd <repo>/deploy/ops/tests && bash run_tests.sh"
```
