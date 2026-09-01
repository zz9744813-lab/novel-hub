#!/usr/bin/env bash
# Behavior tests for deploy/ops/novelforge-release (ops rework round 4).
#
# Runs the REAL controller + upgrade-controller.sh against stub docker/curl/
# install/flock binaries and a real git mirror (execute under WSL/Linux:
# the controller relies on symlinks and flock).
#
# Output contract: the final line is exactly
#   SCENARIOS=19 PASSED=<n> FAILED=<n>
# where every scenario maps to the rework task §10.2 items.
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || {
  printf '%s\n' 'run this suite as root (for the real PostgreSQL and controller-lock scenarios)' >&2
  exit 77
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
CONTROLLER="$REPO_ROOT/deploy/ops/novelforge-release"
UPGRADER="$REPO_ROOT/deploy/ops/upgrade-controller.sh"

SCENARIOS=0
PASSED=0
FAILED=0
SBIN_BEFORE="unset"
if [ "$(id -u)" = "0" ]; then
  SBIN_BEFORE="$(sha256sum /usr/local/sbin/novelforge-release /usr/local/sbin/novelforge-ops 2>/dev/null | sha256sum | cut -d' ' -f1)"
fi
CURRENT_TEST=""
TEST_HARNESS=""

# Scriptable stub behaviour (defaults; scenarios override before run_controller)
QUALIFY_RC=0
VALIDATE_RC=0
ALEMBIC_RC=0
SNAPSHOT_RESTORE_RC=0
QUALIFY_PAYLOAD=""
VALIDATE_PAYLOAD=""
IMAGES_PRESENT=""
STUB_IMAGE_IDS=""
HEALTH_FAIL=0
STUB_ENV_PROBE=0
STUB_ENV_PROBE_KEY=""
STUB_ROTATE_ENV_FILE=""
STUB_ROTATE_ENV_VALUE=""

SANDBOX=""
SRC=""
SHA1=""
SHA2=""
SHA3=""
ROOT=""
SHARED=""
CONF=""

say() { printf '%s\n' "$*"; }

assert_fail_record() { # $1=label $2=detail
  printf '%s -- %s
' "$1" "${2:-}" >>"$TEST_HARNESS/assert-failures"
  say "  assert-fail: $1 (${2:-})"
}
assert_eq() { # got expected label
  if [ "$1" = "$2" ]; then return 0; fi
  assert_fail_record "$3" "got '$1', want '$2'"
  return 0
}
assert_contains() { # content needle label
  if printf '%s' "$1" | grep -qF -- "$2"; then return 0; fi
  assert_fail_record "$3" "missing '$2'"
  return 0
}
assert_not_contains() {
  if printf '%s' "$1" | grep -qF -- "$2"; then
    assert_fail_record "$3" "unexpected '$2'"
  fi
  return 0
}

new_sandbox() {
  : "${TEST_HARNESS:?scenario harness was not initialized}"
  SANDBOX="$TEST_HARNESS/sandbox"
  mkdir -p "$SANDBOX"
  RUN_NO=0
  : >"$SANDBOX/all-outputs.log"
  : >"$SANDBOX/all-calls.log"
  ROOT=$SANDBOX/root
  SHARED=$ROOT/shared
  mkdir -p "$ROOT/lock" "$ROOT/run/candidates" "$ROOT/controller-bin" \
    "$SHARED/data/backups" "$SHARED/candidates" "$ROOT/releases"
  : >"$SANDBOX/docker-image-state"
  printf 'POSTGRES_USER=novelforge\nPOSTGRES_PASSWORD=secret\nPOSTGRES_DB=novelforge\nPRIMARY_API_KEY=key-1\n' \
    >"$SHARED/.env"
  chmod 0640 "$SHARED/.env"

  SRC=$SANDBOX/src
  git init -q -b main "$SRC"
  git -C "$SRC" config core.autocrlf false
  mkdir -p "$SRC/deploy/ops" "$SRC/backend"
  cp -f "$CONTROLLER" "$SRC/deploy/ops/novelforge-release"
  cp -f "$UPGRADER" "$SRC/deploy/ops/upgrade-controller.sh"
  cp -f "$REPO_ROOT/deploy/ops/novelforge-ops" "$SRC/deploy/ops/novelforge-ops"
  cp -f "$REPO_ROOT/deploy/docker-compose.yml" "$SRC/deploy/docker-compose.yml"
  cp -f "$REPO_ROOT/deploy/docker-compose.candidate.yml" \
    "$SRC/deploy/docker-compose.candidate.yml"
  printf '{}\n' >"$SRC/deploy/pack-placeholder"
  git -C "$SRC" add -A
  git -C "$SRC" -c user.email=test@example.com -c user.name=test commit -qm "r1"
  SHA1=$(git -C "$SRC" rev-parse HEAD)
  printf 'change\n' >"$SRC/deploy/change-marker"
  git -C "$SRC" add -A
  git -C "$SRC" -c user.email=test@example.com -c user.name=test commit -qm "r2"
  SHA2=$(git -C "$SRC" rev-parse HEAD)
  printf 'change3\n' >"$SRC/deploy/change-marker"
  git -C "$SRC" add -A
  git -C "$SRC" -c user.email=test@example.com -c user.name=test commit -qm "r3"
  SHA3=$(git -C "$SRC" rev-parse HEAD)

  CONF=$SANDBOX/novelforge-ops.conf
  {
    printf 'REPOSITORY_URL=%s\n' "$SRC"
    printf 'HEALTH_URL=http://127.0.0.1:1/health/ready\n'
  } >"$CONF"

  QUALIFY_RC=0
  VALIDATE_RC=0
  ALEMBIC_RC=0
  SNAPSHOT_RESTORE_RC=0
  BUILD_RC=0
  UP_RC=0
  PROD_UP_RC=0
  QUALIFY_PAYLOAD=$(gate_payload true)
  VALIDATE_PAYLOAD=$(gate_payload true)
  IMAGES_PRESENT=""
  STUB_IMAGE_IDS=""
  HEALTH_FAIL=0
  STUB_ENV_PROBE=0
  STUB_ENV_PROBE_KEY=""
  STUB_ROTATE_ENV_FILE=""
  STUB_ROTATE_ENV_VALUE=""
}

release_env_dir() { printf '%s/root/shared/release-tags' "$SANDBOX"; }

gate_payload() { # $1 = passed(true|false) -> canned production_pack payload file
  local passed=$1
  local out=$SANDBOX/payload-$passed-$RANDOM.json
  {
    printf 'REASONING_ONLY_RESPONSE blocked: reasoning=1c final=0c finish=length\n'
    printf '{\n'
    printf '  "validation": {"passed": true, "errors": []},\n'
    printf '  "model_evidence": {"passed": %s, "blockers": [], "counts": {"gateway_calls": 3}},\n' \
      "$passed"
    printf '  "passed": %s\n' "$passed"
    printf '}\n'
  } >"$out"
  printf '%s\n' "$out"
}

seed_deployed() { # $1 = sha previously deployed
  local sha=$1 release="$ROOT/releases/$1"
  [ -d "$ROOT/repo.git" ] || git clone -q --mirror "$SRC" "$ROOT/repo.git"
  if [ ! -f "$release/.git" ]; then
    git --git-dir="$ROOT/repo.git" worktree add --detach "$release" "$sha" >/dev/null 2>&1
  fi
  ln -sfn "$SHARED/.env" "$release/deploy/.env"
  mkdir -p "$SHARED/release-tags"
  printf 'RELEASE_TAG=%s\n' "$sha" >"$SHARED/release-tags/$sha.env"
  ln -sfn "$release" "$ROOT/current"
  printf '%s\n' "$sha" >"$SHARED/DEPLOYED_SHA"
  seed_images "$sha"
}

seed_images() { # $1 = sha: mark that release's app images as already built
  local sha=$1 svc
  for svc in api worker web; do
    case ",$IMAGES_PRESENT," in
      *",novelforge-$svc:$sha,"*) ;;
      *) IMAGES_PRESENT="novelforge-$svc:$sha,$IMAGES_PRESENT" ;;
    esac
  done
}

OUT_RC=0
run_controller() { # $@ = controller args
  local log=$SANDBOX/docker-calls.log
  : >"$log"
  if (
    export PATH="$HERE/stubs:$PATH"
    export NOVELFORGE_ROOT="$ROOT"
    export NOVELFORGE_CONF="$CONF"
    export NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export NOVELFORGE_CANDIDATE_RUNTIME_DIR="$ROOT/run/candidates"
    export NOVELFORGE_OPS_TARGET="$ROOT/controller-bin/novelforge-release"
    export NOVELFORGE_OPS_WRAPPER_TARGET="$ROOT/controller-bin/novelforge-ops"
    export DOCKER_CALL_LOG="$log"
    export DOCKER_STUB_DIR="$HERE/stubs"
    export DOCKER_IMAGE_STATE="$SANDBOX/docker-image-state"
    export HEALTH_CHECK_ATTEMPTS=1
    export HEALTH_CHECK_INTERVAL=0
    export PG_WAIT_ATTEMPTS=1
    export PG_WAIT_INTERVAL=0
    export STUB_IMAGES_PRESENT="$IMAGES_PRESENT"
    export HEALTH_FAIL="$HEALTH_FAIL"
    export STUB_PS_OUTPUT="${STUB_PS_OUTPUT:-}"
    export STUB_IMAGE_IDS="${STUB_IMAGE_IDS:-}"
    export STUB_ENV_PROBE="$STUB_ENV_PROBE"
    export STUB_ENV_PROBE_KEY="$STUB_ENV_PROBE_KEY"
    export STUB_ROTATE_ENV_FILE="$STUB_ROTATE_ENV_FILE"
    export STUB_ROTATE_ENV_VALUE="$STUB_ROTATE_ENV_VALUE"
    [ -n "$QUALIFY_PAYLOAD" ] && export STUB_QUALIFY_OUTPUT="$QUALIFY_PAYLOAD"
    [ -n "$VALIDATE_PAYLOAD" ] && export STUB_VALIDATE_OUTPUT="$VALIDATE_PAYLOAD"
    export STUB_QUALIFY_RC="$QUALIFY_RC"
    export STUB_VALIDATE_RC="$VALIDATE_RC"
    export STUB_ALEMBIC_RC="$ALEMBIC_RC"
    export STUB_SNAPSHOT_RESTORE_RC="$SNAPSHOT_RESTORE_RC"
    export STUB_BUILD_RC="${BUILD_RC:-0}"
    export STUB_UP_RC="${UP_RC:-0}"
    export STUB_PROD_UP_RC="${PROD_UP_RC:-0}"
    if [ "${RUN_XTRACE:-0}" = "1" ]; then
      bash -x "$CONTROLLER" "$@"
    else
      bash "$CONTROLLER" "$@"
    fi
  ) >"$SANDBOX/last-output" 2>&1; then
    OUT_RC=0
  else
    OUT_RC=$?
  fi
  cat "$SANDBOX/docker-calls.log" >>"$SANDBOX/all-calls.log" 2>/dev/null || true
  RUN_NO=$((RUN_NO + 1))
  {
    printf '=== run %s rc=%s args=%s ===
' "$RUN_NO" "$OUT_RC" "$*"
    cat "$SANDBOX/last-output"
  } >>"$SANDBOX/all-outputs.log"
  return 0
}
RUN_NO=0

last_output() { cat "$SANDBOX/last-output" 2>/dev/null; }
docker_log() { cat "$SANDBOX/docker-calls.log" 2>/dev/null; }
envelope() { printf '%s/root/shared/candidates/%s.envelope' "$SANDBOX" "$1"; }
envelope_state() { sed -n 's/^state=//p' "$(envelope "$1")"; }

prod_mutations() { # calls that would touch/recreate running production containers
  docker_log | grep -cE '^PROJECT=prod CMD=(up|build|run|down)' || true
}

prod_backup_calls() {
  docker_log | grep -c 'pg_dump' || true
}

candidate_projects() { # $1 = calls log; sorted unique candidate project names
  grep -oE '^PROJECT=novelforge-candidate-[0-9a-f-]+' "$1" |
    sed 's/^PROJECT=//' | sort -u
}

current_release_sha() {
  local link
  link=$(readlink "$ROOT/current") || {
    printf ''
    return 0
  }
  basename "$link"
}

merged_candidate_config() { # python yaml merge of base+candidate override
  python3 - "$REPO_ROOT/deploy/docker-compose.yml" \
    "$REPO_ROOT/deploy/docker-compose.candidate.yml" <<'PYEOF'
import sys, yaml

class OverrideTag:
    def __init__(self, value):
        self.value = value

def _override(loader, node):
        if isinstance(node, yaml.SequenceNode):
            return OverrideTag(loader.construct_sequence(node, deep=True))
        if isinstance(node, yaml.MappingNode):
            return OverrideTag(loader.construct_mapping(node, deep=True))
        return OverrideTag(loader.construct_scalar(node))

yaml.SafeLoader.add_constructor("!override", _override)

def load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)

def merge(base, over):
    if isinstance(over, OverrideTag):
        return over.value
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base.get(k), v) if k in base else merge(None, v)
        return out
    return base if over is None else over

base = load(sys.argv[1])
over = load(sys.argv[2])
merged = merge(base, over)
print(yaml.safe_dump(merged, sort_keys=True))
PYEOF
}

# ── scenarios (mapping to rework task §10.2) ────────────────────────────────

s01_build_fail_reject() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "passed" "first candidate publishes passed"
  BUILD_RC=1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "failed" "failed rebuild invalidates envelope"
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects the invalidated sha"
  assert_eq "$(prod_backup_calls)" "0" "zero backups on rejection"
  assert_eq "$(prod_mutations)" "0" "zero production mutations on rejection"
  assert_eq "$(current_release_sha)" "$SHA1" "symlink unchanged"
}

s02_up_fail_reject() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  UP_RC=1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "failed" "up failure invalidates envelope"
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects"
  assert_eq "$(prod_backup_calls)" "0" "zero backups"
  assert_eq "$(current_release_sha)" "$SHA1" "symlink unchanged"
}

s03_alembic_fail_reject() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  ALEMBIC_RC=1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "failed" "alembic failure invalidates envelope"
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects"
  assert_eq "$(prod_backup_calls)" "0" "zero backups"
  assert_eq "$(current_release_sha)" "$SHA1" "symlink unchanged"
}

s04_snapshot_restore_fail_reject() {
  new_sandbox
  printf 'FAKE-DUMP\n' >"$SHARED/data/backups/pre-x.dump"
  run_controller candidate "$SHA1" >/dev/null 2>&1
  local qualify_call
  qualify_call=$(docker_log | grep 'ONEOFF_CMD=.*production_pack.py qualify' | tail -1)
  assert_contains "$qualify_call" "snapshot_check" \
    "model qualification reuses migrated production evidence"
  SNAPSHOT_RESTORE_RC=1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "failed" "snapshot restore failure invalidates envelope"
  assert_contains "$(last_output)" '"production_snapshot":"failed"' \
    "snapshot migration reported separately"
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects"
}

s05_env_rotation_reject_and_reread() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(envelope_state "$SHA1")" "passed" "candidate passes before rotation"
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  sed -i 's/^PRIMARY_API_KEY=.*/PRIMARY_API_KEY=key-2/' "$SHARED/.env"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy refuses after .env rotation"
  assert_contains "$(last_output)" "env_hash mismatch" "env-hash error"
  STUB_ENV_PROBE=1
  STUB_ENV_PROBE_KEY="PRIMARY_API_KEY"
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_contains "$(last_output)" '"PRIMARY_API_KEY":"key-2"' \
    "new candidate observed the rotated secret"
  assert_eq "$(envelope_state "$SHA1")" "passed" "re-candidate re-passed"
  assert_eq "$(find "$ROOT/run/candidates" -type f | wc -l | tr -d ' ')" "0" \
    "candidate secret snapshots are removed on normal exit"

  # Rotate shared .env DURING a new candidate. Every gate must still see the
  # frozen key-2 snapshot, while the passed envelope remains bound to key-2
  # and deploy rejects the now-live key-3 environment.
  STUB_ROTATE_ENV_FILE="$SHARED/.env"
  STUB_ROTATE_ENV_VALUE="key-3"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "0" "mid-run rotation does not mix candidate inputs"
  assert_contains "$(last_output)" '"PRIMARY_API_KEY":"key-2"' \
    "mid-run gate kept using the frozen key-2 snapshot"
  assert_contains "$(cat "$SHARED/.env")" "PRIMARY_API_KEY=key-3" \
    "test hook rotated the live environment"
  assert_not_contains "$(cat "$(envelope "$SHA2")")" "env_hash=$(sha256sum "$SHARED/.env" | cut -d' ' -f1)" \
    "envelope is not rebound to untested key-3"
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects environment rotated during candidate"
  assert_contains "$(last_output)" "env_hash mismatch" "mid-run rotation is fail-closed"
}

s06_no_production_writable_mounts() {
  new_sandbox
  local cfg
  cfg=$(merged_candidate_config)
  assert_eq "$?" "0" "merged candidate config renders"
  assert_not_contains "$cfg" "../data/books" "no books bind in candidate"
  assert_not_contains "$cfg" "../data/exports" "no exports bind in candidate"
  assert_not_contains "$cfg" "../data/imports" "no imports bind in candidate"
  assert_not_contains "$cfg" "../data/backups" "no backups bind in candidate"
  assert_contains "$cfg" "../data/references:/data/references:ro" \
    "references mounted read-only"
  assert_contains "$cfg" "novelforge-candidate-postgres:candidate" \
    "postgres image overridden"
  assert_contains "$cfg" "novelforge_candidate_\${CANDIDATE_ATTEMPT:-unset}" \
    "network is per-attempt"
  assert_not_contains "$cfg" "novelforge_internal" "production network absent"
  assert_eq "$(printf '%s' "$cfg" | grep -c 'new-api:host-gateway')" "2" \
    "api and worker reach only the host-published model gateway"

  # Real-CLI structural check (skipped when docker-compose is absent):
  # render the merged config THROUGH the real controller wrapper
  # (compose_candidate), not through a copy of the command (§4.4).
  if command -v docker-compose >/dev/null 2>&1; then
    local attempt rendered
    attempt=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee
    local rendered
    rendered=$(
      (
        export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        export NOVELFORGE_CONTROLLER_SOURCABLE=1
        export NOVELFORGE_ROOT="$ROOT" NOVELFORGE_CONF="$CONF"
        export NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
        export NOVELFORGE_CANDIDATE_RUNTIME_DIR="$ROOT/run/candidates"
        export REPOSITORY_URL="$SRC" DEPLOY_BRANCH=main
        export HEALTH_URL="http://127.0.0.1:1/health/ready"
        # shellcheck disable=SC1090  # controller path is the test subject
        source "$CONTROLLER"
        fetch_main
        validate_sha "$SHA1"
        local release
        release=$(prepare_release "$SHA1")
        create_attempt_env "$attempt"
        compose_candidate "$release" "$attempt" config
      ) 2>"$SANDBOX/real.err"
    ) || {
      assert_fail_record "real controller compose config failed" "$(head -c 200 "$SANDBOX/real.err")"
      return 0
    }
    assert_not_contains "$rendered" "novelforge_internal"       "real merged config has no production network"
    assert_not_contains "$rendered" "novelforge_candidate_unset"       "real merged config has no unset attempt network"
    assert_contains "$rendered" "novelforge_candidate_$attempt"       "real merged config uses the exact attempt network"
    assert_eq "$(printf '%s' "$rendered" | grep -c 'new-api=host-gateway')" "2" \
      "real merged config maps the model gateway for api and worker"
    if printf '%s' "$rendered" | grep -qE '\.\./data/(books|exports|imports|backups)'; then
      assert_fail_record "real merged config has production-writable bind"
    fi
    assert_eq "$(printf '%s' "$rendered" | grep -c 'read_only: true')" "2"       "references read-only in real merged config"
  fi
}

s07_unique_attempt_generation() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(candidate_projects "$SANDBOX/all-calls.log" | wc -l | tr -d ' ')" "2" \
    "each candidate attempt uses a unique project"
  assert_eq "$(grep -c 'CMD=down' "$SANDBOX/all-calls.log")" "2" \
    "each attempt cleaned its own stack"
  assert_eq "$(envelope_state "$SHA1")" "passed" \
    "second attempt still passes on a fresh generation"
}

s15_deployed_sha_candidate_tag_safety() {
  new_sandbox
  seed_deployed "$SHA1"
  # Record the current digest of the deployed release tag.
  local map=$SANDBOX/ids.txt
  : >"$map"
  local svc
  for svc in api worker web; do
    printf 'novelforge-%s:%s sha256:deployed-digest
' "$svc" "$SHA1" >>"$map"
  done
  STUB_IMAGE_IDS="$map"
  BUILD_RC=1
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$(sed -n 's/^state=//p' "$(envelope "$SHA1")")" "failed"     "failed candidate on deployed sha is marked failed"
  # The deployed release-tag digests must be untouched by the failed attempt.
  for svc in api worker web; do
    assert_eq "$(sed -n "s#^novelforge-$svc:$SHA1 ##p" "$map")" "sha256:deployed-digest"       "release tag digest of $svc unchanged"
  done
  # The build must have targeted the per-attempt candidate tag, not the
  # release tag: the attempt env pins RELEASE_TAG=candidate-<attempt>.
  local attempt
  attempt=$(sed -n 's/^attempt_id=//p' "$(envelope "$SHA1")")
  assert_contains "$(docker_log)" "RELEASE_TAG=candidate-$attempt"     "attempt env pins the candidate tag"

  # A later PASSED candidate for the currently deployed SHA still cannot
  # replace that SHA's existing immutable release tags with different bytes.
  BUILD_RC=0
  STUB_IMAGE_IDS=""
  run_controller candidate "$SHA1" >/dev/null 2>&1
  attempt=$(sed -n 's/^attempt_id=//p' "$(envelope "$SHA1")")
  : >"$map"
  local digest_line candidate_id
  digest_line=$(sed -n 's/^image_digests=//p' "$(envelope "$SHA1")")
  for svc in api worker web; do
    candidate_id=$(printf '%s' "$digest_line" | tr '|' '\n' | sed -n "s/^$svc://p")
    printf 'novelforge-%s:candidate-%s %s\n' "$svc" "$attempt" "$candidate_id" >>"$map"
    printf 'novelforge-%s:%s sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\n' \
      "$svc" "$SHA1" >>"$map"
  done
  STUB_IMAGE_IDS="$map"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "passed re-candidate cannot re-point deployed sha"
  assert_contains "$(last_output)" "refusing to re-point deployed release tag" \
    "immutable deployed tag refusal is explicit"
  assert_eq "$(prod_backup_calls)" "0" "immutable-tag refusal happens before backup"
  assert_not_contains "$(docker_log)" "IMAGE_TAG" "no release tag changed on refusal"
}

s08_digest_repoint_reject() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  local map=$SANDBOX/ids.txt
  : >"$map"
  local svc attempt
  attempt=$(sed -n 's/^attempt_id=//p' "$(envelope "$SHA1")")
  for svc in api worker web; do
    printf 'novelforge-%s:candidate-%s sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff\n' \
      "$svc" "$attempt" >>"$map"
  done
  STUB_IMAGE_IDS="$map"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "deploy rejects re-pointed image"
  assert_contains "$(last_output)" "digest mismatch" "digest mismatch error"
  assert_eq "$(prod_backup_calls)" "0" "zero backups on digest mismatch"
}

s16_real_postgres_snapshot_migration() {
  new_sandbox
  command -v psql >/dev/null 2>&1 || { assert_fail_record "psql missing"; return 0; }
  local WIN_PY="/mnt/f/gpt/.worktrees/novel-hub-model-gate-20260825/.venv/Scripts/python.exe"
  [ -f "$WIN_PY" ] || { assert_fail_record "backend venv python missing"; return 0; }
  local role_pw=nfcandidate_testpw
  local pg_role="nfcandidate_$$" src_db="nf_snap_src_$$" dst_db="nf_snap_dst_$$"
  cleanup_pg() {
    su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $src_db\" >/dev/null" || true
    su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $dst_db\" >/dev/null" || true
    su postgres -c "psql -qc \"DROP ROLE IF EXISTS $pg_role\" >/dev/null" || true
  }
  trap cleanup_pg EXIT
  cleanup_pg
  su postgres -c "psql -qc \"CREATE ROLE $pg_role LOGIN PASSWORD '$role_pw'\" >/dev/null"
  su postgres -c "createdb -O $pg_role $src_db"
  # Old schema: migrate the worktree backend to the revision BEFORE head.
  local backend=$REPO_ROOT/backend
  local wsl_ip
  wsl_ip=$(hostname -I | awk '{print $1}')
  local url="postgresql+asyncpg://$pg_role:$role_pw@$wsl_ip:5432/$src_db"
  local parent_rev
  parent_rev=$( (cd "$backend" && "$WIN_PY" -m alembic history) 2>/dev/null | sed -n '1s/ ->.*//p')
  [ -n "$parent_rev" ] || { assert_fail_record "cannot read alembic history"; return 0; }
  (cd "$backend" && \
    WSLENV="${WSLENV:+$WSLENV:}DATABASE_URL/w" DATABASE_URL="$url" \
    "$WIN_PY" -m alembic upgrade "$parent_rev") \
    >"$SANDBOX/alembic-src.log" 2>&1
  # old schema + one data row
  su postgres -c "psql -q -d $src_db -c \"INSERT INTO books (id, title) VALUES (gen_random_uuid(), 'snapshot-row')\"" >/dev/null
  su postgres -c "pg_dump --format=custom $src_db" >"$SANDBOX/snap.dump"
  # restore into the dst db through the controller's exec mechanism
  su postgres -c "createdb -O $pg_role $dst_db"
  su postgres -c "pg_restore --dbname=$dst_db" <"$SANDBOX/snap.dump" >/dev/null 2>&1
  # migrate dst to head with the real alembic chain
  local dst_url="postgresql+asyncpg://$pg_role:$role_pw@$wsl_ip:5432/$dst_db"
  (cd "$backend" && \
    WSLENV="${WSLENV:+$WSLENV:}DATABASE_URL/w" DATABASE_URL="$dst_url" \
    "$WIN_PY" -m alembic upgrade head) \
    >"$SANDBOX/alembic-dst.log" 2>&1
  local head_ver rows
  head_ver=$(su postgres -c "psql -tA -d $dst_db -c 'SELECT version_num FROM alembic_version'" 2>/dev/null)
  rows=$(su postgres -c "psql -tA -d $dst_db -c 'SELECT count(*) FROM books'" 2>/dev/null)
  assert_eq "$rows" "1" "snapshot data survived restore+migrate"
  assert_contains "$( (cd "$backend" && "$WIN_PY" -m alembic heads) 2>/dev/null)" "$head_ver"     "snapshot db migrated to alembic head"
  cleanup_pg
  trap - EXIT
}

s09_missing_failed_stale_envelopes() {
  new_sandbox
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "missing envelope refused"
  assert_eq "$(prod_backup_calls)" "0" "missing: zero backups"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  QUALIFY_RC=1
  QUALIFY_PAYLOAD=$(gate_payload false)
  run_controller candidate "$SHA2" >/dev/null 2>&1
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "failed envelope refused"
  assert_eq "$(prod_backup_calls)" "0" "failed: zero backups"
  run_controller candidate "$SHA3" >/dev/null 2>&1
  sed -i "s/^created=.*/created=$(( $(date -u +%s) - 90000 ))/" "$(envelope "$SHA3")"
  run_controller deploy "$SHA3" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "stale envelope refused"
  assert_eq "$(prod_backup_calls)" "0" "stale: zero backups"
  assert_eq "$(prod_mutations)" "0" "no production mutations across refusals"
}

s10_malformed_wrong_sha_envelopes() {
  new_sandbox
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  run_controller candidate "$SHA1" >/dev/null 2>&1
  grep -v '^attempt_id=' "$(envelope "$SHA1")" >"$SANDBOX/m.env"
  cp "$SANDBOX/m.env" "$(envelope "$SHA1")"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "malformed envelope refused"
  assert_eq "$(prod_backup_calls)" "0" "malformed: zero backups"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  sed -i "s/^sha=.*/sha=$SHA1/" "$(envelope "$SHA2")"
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "wrong-sha envelope refused"
  assert_contains "$(last_output)" "candidate envelope sha mismatch" "sha binding enforced"
  assert_eq "$(prod_backup_calls)" "0" "wrong-sha: zero backups"

  run_controller candidate "$SHA1" >/dev/null 2>&1
  sed -i 's/^created=.*/created=not-a-number/' "$(envelope "$SHA1")"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "non-numeric created refused"
  assert_contains "$(last_output)" "non-numeric created" "created validation is fail-closed"

  run_controller candidate "$SHA1" >/dev/null 2>&1
  sed -i 's/^image_digests=.*/image_digests=/' "$(envelope "$SHA1")"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "missing image digests refused"
  assert_contains "$(last_output)" "image_digests must cover" "digest completeness enforced"

  run_controller candidate "$SHA1" >/dev/null 2>&1
  printf 'unexpected_key=value\n' >>"$(envelope "$SHA1")"
  run_controller deploy "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "71" "unknown envelope key refused"
  assert_eq "$(prod_backup_calls)" "0" "all malformed variants stop before backup"
}

s17_candidate_to_deploy_success() {
  new_sandbox
  seed_deployed "$SHA1"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "0" "candidate succeeds"
  assert_eq "$(envelope_state "$SHA2")" "passed" "candidate publishes passed envelope"
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "0" "deploy accepts its own candidate envelope"
  assert_eq "$(current_release_sha)" "$SHA2" "current switches to candidate sha"
  assert_eq "$(prod_backup_calls)" "1" "successful deploy makes one backup"
  assert_contains "$(docker_log)" "CMD=up ARGS=-d --no-deps api worker web" \
    "cutover activates application services only"
  assert_not_contains "$(docker_log)" "CMD=up ARGS=-d postgres" \
    "cutover never recreates postgres"
  assert_contains "$(docker_log)" "IMAGE_TAG SOURCE=novelforge-api:candidate-" \
    "candidate api artifact is published"
  assert_contains "$(last_output)" '"ok":true,"action":"deploy"' \
    "deploy reports structured success"
  assert_eq "$(sha256sum "$ROOT/controller-bin/novelforge-release" | cut -d' ' -f1)" \
    "$(sha256sum "$ROOT/releases/$SHA2/deploy/ops/novelforge-release" | cut -d' ' -f1)" \
    "post-deploy controller pair matches the release"
}

s11_safe_controller_upgrade() {
  new_sandbox
  seed_deployed "$SHA1"
  # Real install/mv/mktemp (NO stub PATH): the upgrader targets are pointed
  # INSIDE the sandbox so /usr/local/sbin is never touched (P0-D).
  local log=$SANDBOX/docker-calls.log
  : >"$log"
  (
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    export NOVELFORGE_ROOT="$ROOT"
    export NOVELFORGE_CONF="$CONF"
    export NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export NOVELFORGE_OPS_TARGET="$SANDBOX/usr/local/sbin/novelforge-release"
    export NOVELFORGE_OPS_WRAPPER_TARGET="$SANDBOX/usr/local/sbin/novelforge-ops"
    bash "$UPGRADER" "$SHA2"
  ) >"$SANDBOX/upgrade-output" 2>&1
  assert_eq "$?" "0" "upgrade-controller exits zero"
  if grep -qE '^PROJECT=' "$log"; then
    assert_fail_record "upgrade called docker compose" "production at risk"
  fi
  assert_contains "$(cat "$SANDBOX/upgrade-output")" '"action":"upgrade-controller"'     "structured upgrade output"
  local rel_sha
  rel_sha=$(sed -n 's/.*"sha":"\([0-9a-f]\{40\}\)".*/\1/p' "$SANDBOX/upgrade-output" | head -1)
  assert_eq "$(sha256sum "$SANDBOX/usr/local/sbin/novelforge-release" | cut -d' ' -f1)"     "$(sha256sum "$ROOT/releases/$rel_sha/deploy/ops/novelforge-release" | cut -d' ' -f1)"     "installed controller hash matches release source"

  # Force the SECOND install mv to fail after the first file switched. The
  # upgrader must restore both old files rather than leave a mixed pair.
  printf 'old-controller\n' >"$SANDBOX/usr/local/sbin/novelforge-release"
  printf 'old-wrapper\n' >"$SANDBOX/usr/local/sbin/novelforge-ops"
  local old_release_hash old_wrapper_hash rollback_rc=0
  old_release_hash=$(sha256sum "$SANDBOX/usr/local/sbin/novelforge-release" | cut -d' ' -f1)
  old_wrapper_hash=$(sha256sum "$SANDBOX/usr/local/sbin/novelforge-ops" | cut -d' ' -f1)
  mkdir -p "$SANDBOX/failbin"
  cat >"$SANDBOX/failbin/mv" <<'MVSTUB'
#!/usr/bin/env bash
last=${!#}
source_arg=${@: -2:1}
if [[ $source_arg == *.new.* ]] && [ "$last" = "${NOVELFORGE_OPS_WRAPPER_TARGET:-}" ]; then
  exit 88
fi
exec /bin/mv "$@"
MVSTUB
  chmod 0755 "$SANDBOX/failbin/mv"
  if (
    export PATH="$SANDBOX/failbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    export NOVELFORGE_ROOT="$ROOT" NOVELFORGE_CONF="$CONF" NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export NOVELFORGE_OPS_TARGET="$SANDBOX/usr/local/sbin/novelforge-release"
    export NOVELFORGE_OPS_WRAPPER_TARGET="$SANDBOX/usr/local/sbin/novelforge-ops"
    bash "$UPGRADER" "$SHA2"
  ) >"$SANDBOX/rollback-output" 2>&1; then
    rollback_rc=0
  else
    rollback_rc=$?
  fi
  assert_eq "$rollback_rc" "88" "second-file switch failure is surfaced"
  assert_eq "$(sha256sum "$SANDBOX/usr/local/sbin/novelforge-release" | cut -d' ' -f1)" \
    "$old_release_hash" "controller restored after pair switch failure"
  assert_eq "$(sha256sum "$SANDBOX/usr/local/sbin/novelforge-ops" | cut -d' ' -f1)" \
    "$old_wrapper_hash" "wrapper restored after pair switch failure"

  # Lock contention: a held release lock must make the upgrader refuse (75).
  local lock_rc=0
  if (
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    export NOVELFORGE_ROOT="$ROOT" NOVELFORGE_CONF="$CONF" NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export NOVELFORGE_OPS_TARGET="$SANDBOX/usr/local/sbin/novelforge-release"
    export NOVELFORGE_OPS_WRAPPER_TARGET="$SANDBOX/usr/local/sbin/novelforge-ops"
    exec 9>"$ROOT/lock/nf.lock"
    flock 9
    bash "$UPGRADER" "$SHA2"
  ) >"$SANDBOX/lock-output" 2>&1; then
    lock_rc=0
  else
    lock_rc=$?
  fi
  assert_eq "$lock_rc" "75" "concurrent lock contention refused"
}

s12_migration_failure_no_change() {
  new_sandbox
  seed_deployed "$SHA1"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  seed_images "$SHA2"
  : >"$SANDBOX/docker-calls.log"
  ALEMBIC_RC=1
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$(current_release_sha)" "$SHA1" "symlink unchanged"
  if docker_log | grep -q 'CMD=up ARGS='; then
    assert_fail_record "migration failure still ran production up"
  fi
  assert_contains "$(docker_log)" "pg_dump" "backup happened before migration"

  # A failed application activation after switching must still restore the
  # previous symlink and enter the normal rollback path.
  ALEMBIC_RC=0
  PROD_UP_RC=1
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$OUT_RC" "70" "application activation failure rejects deploy"
  assert_eq "$(current_release_sha)" "$SHA1" "activation failure restores previous symlink"
  assert_contains "$(last_output)" '"rollback":"failed"' \
    "activation failure reports rollback result structurally"
}

s13_health_failure_rollback() {
  new_sandbox
  seed_deployed "$SHA1"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  seed_images "$SHA2"
  HEALTH_FAIL=1
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$(current_release_sha)" "$SHA1" "symlink rolled back"
  assert_contains "$(docker_log)" "CMD=up ARGS=-d --no-deps api worker web" \
    "rollback restarted previous services"
  assert_contains "$(last_output)" '"rollback":"failed"' \
    "rollback failure reported structurally"
}

s14_status_provenance_variants() {
  new_sandbox
  seed_deployed "$SHA1"
  cat >"$SANDBOX/ps.json" <<EOF
{"Name":"novelforge-api-1","Service":"api","Image":"novelforge-api:$SHA1","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-web-1","Service":"web","Image":"novelforge-web:$SHA2","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA2/deploy"}
{"Name":"novelforge-postgres-1","Service":"postgres","Image":"novelforge-postgres:16-config-v2","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA2/deploy"}
EOF
  STUB_PS_OUTPUT="$SANDBOX/ps.json" run_controller status >/dev/null 2>&1
  assert_contains "$(last_output)" '"mixed_release":true' "mixed detected"
  assert_contains "$(last_output)" '"app_mixed_release":true' "app mixed flag"
  assert_contains "$(last_output)" "\"api\":\"$SHA1\"" "api provenance"
  assert_contains "$(last_output)" "\"postgres\":\"$SHA2\"" "postgres provenance"
  assert_contains "$(last_output)" '"infra_version":{"postgres":"novelforge-postgres:16-config-v2"' \
    "infra version reported"
  assert_contains "$(last_output)" '"missing_services":["worker","redis"]' \
    "missing services listed"
  assert_contains "$(last_output)" '"provenance_complete":false' "incomplete flagged"
  cat >"$SANDBOX/ps.json" <<EOF
{"Name":"novelforge-api-1","Service":"api","Image":"novelforge-api:$SHA1","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-web-1","Service":"web","Image":"novelforge-web:$SHA1","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-worker-1","Service":"worker","Image":"novelforge-worker:$SHA1","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-postgres-1","Service":"postgres","Image":"novelforge-postgres:16-config-v2","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-redis-1","Service":"redis","Image":"novelforge-redis:7-config-v2","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
EOF
  {
    printf '['
    paste -sd, "$SANDBOX/ps.json"
    printf ']\n'
  } >"$SANDBOX/ps-array.json"
  STUB_PS_OUTPUT="$SANDBOX/ps-array.json" run_controller status >/dev/null 2>&1
  assert_contains "$(last_output)" '"mixed_release":false' "consistent provenance"
  assert_contains "$(last_output)" '"provenance_complete":true' "complete provenance"
  assert_contains "$(last_output)" '"unexpected_services":[]' "no unexpected services"

  sed -i '/"Service":"worker"/ s#"Labels":"[^"]*"#"Labels":""#' "$SANDBOX/ps.json"
  STUB_PS_OUTPUT="$SANDBOX/ps.json" run_controller status >/dev/null 2>&1
  assert_contains "$(last_output)" '"invalid_provenance_services":["worker"]' \
    "present service with missing release label is invalid"
  assert_contains "$(last_output)" '"provenance_complete":false' \
    "missing provenance label prevents completeness"
  assert_contains "$(last_output)" '"mixed_release":true' \
    "missing app provenance is fail-closed as mixed"
}

s18_reused_release_integrity_is_fail_closed() {
  new_sandbox
  seed_deployed "$SHA1"
  local release="$ROOT/releases/$SHA1"

  # A directory named after SHA1 must never be trusted when its actual
  # checkout points at another commit. Rejection happens before any Compose
  # build/gate call and before an envelope can be published.
  git -C "$release" reset --hard "$SHA2" >/dev/null
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "73" "mismatched release HEAD is rejected"
  assert_contains "$(last_output)" "release HEAD mismatch" "HEAD mismatch is explicit"
  assert_eq "$(wc -l <"$SANDBOX/all-calls.log" | tr -d ' ')" "0" \
    "HEAD mismatch causes zero Compose calls"
  [ ! -e "$(envelope "$SHA1")" ] \
    || assert_fail_record "HEAD mismatch must not publish an envelope"

  # Even with the expected HEAD, tracked local edits would make the built
  # bytes differ from the commit bound into the candidate envelope.
  git -C "$release" reset --hard "$SHA1" >/dev/null
  printf '\n# tracked tamper\n' >>"$release/deploy/docker-compose.yml"
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "73" "modified tracked release is rejected"
  assert_contains "$(last_output)" "modified tracked files" \
    "tracked modification is explicit"
  assert_eq "$(wc -l <"$SANDBOX/all-calls.log" | tr -d ' ')" "0" \
    "tracked modification causes zero Compose calls"

  # Untracked/ignored residue can also alter a Docker build context and must
  # be rejected even though ordinary git diff remains clean.
  git -C "$release" reset --hard "$SHA1" >/dev/null
  printf 'untracked build-context tamper\n' >"$release/deploy/untracked-tamper"
  run_controller candidate "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "73" "untracked release residue is rejected"
  assert_contains "$(last_output)" "untracked or ignored files" \
    "untracked residue is explicit"
  assert_eq "$(wc -l <"$SANDBOX/all-calls.log" | tr -d ' ')" "0" \
    "untracked residue causes zero Compose calls"
}

s19_rollback_release_integrity_is_fail_closed() {
  new_sandbox
  seed_deployed "$SHA1"
  local release="$ROOT/releases/$SHA1"

  git -C "$release" reset --hard "$SHA2" >/dev/null
  run_controller rollback "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "73" "rollback rejects mismatched release HEAD"
  assert_contains "$(last_output)" "release HEAD mismatch" \
    "rollback HEAD mismatch is explicit"
  assert_eq "$(wc -l <"$SANDBOX/all-calls.log" | tr -d ' ')" "0" \
    "rollback mismatch causes zero Compose calls"

  git -C "$release" reset --hard "$SHA1" >/dev/null
  printf 'rollback tamper\n' >"$release/deploy/untracked-tamper"
  run_controller rollback "$SHA1" >/dev/null 2>&1
  assert_eq "$OUT_RC" "73" "rollback rejects untracked release residue"
  assert_contains "$(last_output)" "untracked or ignored files" \
    "rollback residue is explicit"
  assert_eq "$(wc -l <"$SANDBOX/all-calls.log" | tr -d ' ')" "0" \
    "rollback residue causes zero Compose calls"
}

# ── precise scenario accounting (§10.2-14) ─────────────────────────────────

# Error signatures that mark a scenario's stderr as dirty (acceptance P0-D:
# unexpected bash/python errors must fail the scenario, never print through).
STDERR_ERROR_RE='no such file|unbound variable|unterminated|permission denied|integer expected|traceback|syntax error|command not found|ambiguous argument|invalid argument|not a valid|unexpected'

scenario() { # $1=name $2=fn
  CURRENT_TEST=$1
  SCENARIOS=$((SCENARIOS + 1))
  TEST_HARNESS=$(mktemp -d)
  : >"$TEST_HARNESS/assert-failures"
  : >"$TEST_HARNESS/scenario.err"
  local rc=0
  # Do not put the strict subshell on the left side of `||`/`if`: Bash would
  # silently disable errexit inside every scenario function. Temporarily
  # relax only the parent while the child enforces its own -e contract.
  set +e
  (
    set -Eeuo pipefail
    trap 'echo "uncaught error at line $LINENO (rc=$?)" >&2' ERR
    "$2"
  ) >"$TEST_HARNESS/scenario.out" 2>"$TEST_HARNESS/scenario.err"
  rc=$?
  set -e

  local failures=0
  if [ -s "$TEST_HARNESS/assert-failures" ]; then
    failures=$(grep -c . "$TEST_HARNESS/assert-failures")
  fi
  local dirty=""
  if [ -s "$TEST_HARNESS/scenario.err" ] \
    && grep -qiE "$STDERR_ERROR_RE" "$TEST_HARNESS/scenario.err"; then
    dirty=yes
  fi

  if [ "$rc" -eq 0 ] && [ "$failures" = "0" ] && [ -z "$dirty" ]; then
    PASSED=$((PASSED + 1))
    say "PASS [$SCENARIOS/19] $CURRENT_TEST"
  else
    FAILED=$((FAILED + 1))
    say "FAIL [$SCENARIOS/19] $CURRENT_TEST (rc=$rc assertions=$failures dirty_stderr=$dirty)"
    sed -n '1,10p' "$TEST_HARNESS/assert-failures" 2>/dev/null
    { grep -iE "$STDERR_ERROR_RE" "$TEST_HARNESS/scenario.err" 2>/dev/null || true; } \
      | sed -n '1,4p'
    [ -f "$TEST_HARNESS/scenario.out" ] \
      && say "  scenario-output: $(head -c 300 "$TEST_HARNESS/scenario.out")"
  fi
  if [ "${KEEP_FAILED:-0}" = "1" ]     && { [ "$rc" -ne 0 ] || [ "$failures" != "0" ] || [ -n "$dirty" ]; }; then
    say "  (sandbox kept: $TEST_HARNESS)"
  else
    rm -rf "$TEST_HARNESS"
  fi
  SANDBOX=""
  TEST_HARNESS=""
}

scenario "same-SHA pass then build failure -> deploy rejects" s01_build_fail_reject
scenario "same-SHA pass then up failure -> deploy rejects" s02_up_fail_reject
scenario "same-SHA pass then alembic failure -> deploy rejects" s03_alembic_fail_reject
scenario "same-SHA pass then snapshot restore failure -> deploy rejects" s04_snapshot_restore_fail_reject
scenario "candidate on the deployed sha never touches its release-tag digests" s15_deployed_sha_candidate_tag_safety
scenario "shared .env rotation -> deploy rejects; re-candidate reads rotated value" s05_env_rotation_reject_and_reread
scenario "merged candidate config has no production-writable mounts" s06_no_production_writable_mounts
scenario "candidate attempts use unique project generations" s07_unique_attempt_generation
scenario "re-pointed image digest -> deploy rejects" s08_digest_repoint_reject
scenario "missing/failed/stale envelopes -> deploy rejects with zero backups" s09_missing_failed_stale_envelopes
scenario "real PostgreSQL restore + alembic migrate keeps data and reaches head" s16_real_postgres_snapshot_migration
scenario "malformed/wrong-sha envelopes -> deploy rejects with zero backups" s10_malformed_wrong_sha_envelopes
scenario "safe controller upgrade with zero production compose calls" s11_safe_controller_upgrade
scenario "candidate-produced envelope completes a successful deploy" s17_candidate_to_deploy_success
scenario "migration failure leaves current and services unchanged" s12_migration_failure_no_change
scenario "health failure rolls back and reports structurally" s13_health_failure_rollback
scenario "status provenance variants incl. infra version and missing services" s14_status_provenance_variants
scenario "reused release HEAD and tracked bytes are fail-closed" s18_reused_release_integrity_is_fail_closed
scenario "rollback rejects mismatched or dirty release bytes" s19_rollback_release_integrity_is_fail_closed

# P0-D guard: the test run must never modify the host's /usr/local/sbin.
if [ "$(id -u)" = "0" ]; then
  after_sbin="$(sha256sum /usr/local/sbin/novelforge-release /usr/local/sbin/novelforge-ops 2>/dev/null | sha256sum | cut -d' ' -f1)"
  if [ "$SBIN_BEFORE" != "${after_sbin:-absent}" ]; then
    FAILED=$((FAILED + 1))
    say "FAIL: /usr/local/sbin controller files changed during the run"
  fi
fi

say ""
say "SCENARIOS=$SCENARIOS PASSED=$PASSED FAILED=$FAILED"
[ "$FAILED" -eq 0 ]
