# NovelForge release controller: candidate / deploy / status

Authoritative flow for `deploy/ops/novelforge-release` (rework round 3).
Older documents describing `release-env`, manual controller installs after a
deploy, or the old `up -d postgres`-first deploy are obsolete.

## Flow

```
candidate <sha>
  fetch main -> verify sha is an ancestor -> prepare release worktree
  (worktree + .env symlink + release-tags/<sha>.env + data symlink)
  -> validate interpolation with the production compose file
  -> freeze a 0600 per-attempt env snapshot
     ($SHARED/candidates/<attempt>.env = shared .env snapshot
      + RELEASE_TAG=candidate-<attempt> + CANDIDATE_ATTEMPT=<uuid>)
  -> invalidate the sha's envelope to state=running (deploy refuses from here)
  -> build all five images under the ISOLATED compose project
     novelforge-candidate-<attempt> (candidate-only infra tags, own network,
     own volumes, no production data bind mounts; references are read-only)
  -> throwaway db #1: alembic upgrade head            (migration_fresh)
  -> throwaway db #2: restore newest production backup
     via `compose exec -T postgres` stdin + pg_restore, then alembic
     upgrade head against it                          (migration_snapshot)
     (no backup present -> migration_snapshot=skipped)
  -> production_pack validate + qualify (real gateway; evidence written to
     the throwaway db only)
  -> record api/worker/web image digests, publish state=passed envelope
  -> tear down the attempt project (containers/volumes/network + attempt env
     file; images kept)

deploy <sha>
  verify candidate envelope strictly (fail-closed):
    sha binding, attempt_id uuid, created numeric+fresh, state=passed,
    env_hash == current shared .env hash (rotation forces re-candidate),
    image digests == current candidate-tag digests
  -> publish release artifact tags novelforge-<svc>:<sha> from the verified
     candidate digests (a failed candidate can never re-point release tags)
  -> backup via `compose exec` only (postgres must be running; else abort)
  -> migration as ONE-OFF container (--rm --no-deps)
  -> switch symlink -> compose up -> health
  -> on unhealthy: switch back + up previous release (its own immutable
     image tags) + structured rollback report
  -> install controller, record DEPLOYED_SHA

status
  current sha/health plus per-service provenance, infra_version (postgres/
  redis images), missing_services, unexpected_services, provenance_complete,
  and mixed_release / app_mixed_release comparing ONLY the three app
  services against the current symlink.
```

## Guarantees

- A failed or interrupted candidate leaves the envelope state=running/failed:
  deploy refuses. No build failure can be carried by an older passed result.
- A failed candidate never re-points release artifact tags: app images are
  built as `novelforge-<svc>:candidate-<attempt>` and only retagged to
  `novelforge-<svc>:<sha>` by a deploy that passed the gate.
- Candidate never touches production data: books/exports/imports are
  per-attempt named volumes, references are read-only, the postgres backups
  bind is absent, the network is attempt-unique.
- Snapshot restore runs inside the RUNNING candidate postgres (`compose exec
  -T`, dump streamed via stdin) — never a second container mounting the same
  PGDATA.
- Secrets: the attempt env snapshot is 0600 and deleted on attempt exit; the
  envelope keeps only its sha256.
- deploy refuses when the shared .env hash changed since the candidate
  (rotation -> re-candidate).
- Concurrent runs are excluded by the release lock; candidate/deploy/
  upgrade-controller all share it.
- Nothing is deleted: releases, backups, candidate envelopes and raw JSON
  accumulate for audit.

## First-time activation of a new controller

`install_release_controller` runs after a healthy switch, so the FIRST deploy
after merging a controller change still executes the previously installed
controller logic. To activate the new controller WITHOUT a production deploy,
run from the root console:

```bash
# fetch the upgrader itself pinned to the target SHA (no repo checkout needed)
curl -fsSL https://raw.githubusercontent.com/zz9744813-lab/novel-hub/<SHA>/deploy/ops/upgrade-controller.sh   -o /tmp/upgrade-controller.sh && bash /tmp/upgrade-controller.sh <SHA>
```

`upgrade-controller.sh` shares the release lock, verifies the sha is on
main, prepares the release worktree, bash -n-checks both controller files,
and atomically installs them with hash verification (auto-restore on
failure). `bootstrap-console.sh` requires `OPS_COMMIT` (40-hex, validated) —
it no longer carries a hardcoded commit.

## Infra image contract (plan A)

- api/worker/web: `novelforge-<svc>:${RELEASE_TAG}` — release SHA bound.
- postgres/redis: `novelforge-postgres:16-config-v2` /
  `novelforge-redis:7-config-v2` — immutable infra versions, built by
  candidate under candidate-only tags and never retagged by a gate.
- `mixed_release` compares only api/web/worker; `infra_version` reports the
  running postgres/redis images.

## Tests

`deploy/ops/tests/run_tests.sh` — 16 scenarios, exact final line
`SCENARIOS=16 PASSED=<n> FAILED=<n>`. Run as root inside WSL/Linux (the
controller needs symlinks and flock); docker-compose enables the real-CLI
merged-config check in scenario 6; scenario 16 exercises a REAL PostgreSQL
(restore + alembic + data survival). Guard: /usr/local/sbin controller files
must be byte-identical before and after the run.

```bash
wsl -u root -e bash -c "cd <repo>/deploy/ops/tests && bash run_tests.sh"
```
