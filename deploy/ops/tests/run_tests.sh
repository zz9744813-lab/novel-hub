#!/usr/bin/env bash
# Behavior tests for deploy/ops/novelforge-release (ops rework round 2).
#
# Runs the REAL controller + upgrade-controller.sh against stub docker/curl/
# install/flock binaries and a real git mirror (execute under WSL/Linux:
# the controller relies on symlinks and flock).
#
# Output contract (acceptance §10.2-14): the final line is exactly
#   SCENARIOS=14 PASSED=<n> FAILED=<n>
# where every scenario maps to the rework task §10.2 items.
set -Eeuo pipefail

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
' "$1" "${2:-}" >>"$SANDBOX/assert-failures"
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

cleanup() {
  [ -n "$SANDBOX" ] && rm -rf "$SANDBOX"
  return 0
}
trap cleanup EXIT

new_sandbox() {
  SANDBOX=$(mktemp -d)
  RUN_NO=0
  : >"$SANDBOX/all-outputs.log"
  : >"$SANDBOX/all-calls.log"
  ROOT=$SANDBOX/root
  SHARED=$ROOT/shared
  mkdir -p "$ROOT/lock" "$SHARED/data/backups" "$SHARED/candidates" "$ROOT/releases"
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
  QUALIFY_PAYLOAD=$(gate_payload true)
  VALIDATE_PAYLOAD=$(gate_payload true)
  IMAGES_PRESENT=""
  STUB_IMAGE_IDS=""
  HEALTH_FAIL=0
  STUB_ENV_PROBE=0
  STUB_ENV_PROBE_KEY=""
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
  (
    export PATH="$HERE/stubs:$PATH"
    export NOVELFORGE_ROOT="$ROOT"
    export NOVELFORGE_CONF="$CONF"
    export NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export DOCKER_CALL_LOG="$log"
    export DOCKER_STUB_DIR="$HERE/stubs"
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
    [ -n "$QUALIFY_PAYLOAD" ] && export STUB_QUALIFY_OUTPUT="$QUALIFY_PAYLOAD"
    [ -n "$VALIDATE_PAYLOAD" ] && export STUB_VALIDATE_OUTPUT="$VALIDATE_PAYLOAD"
    export STUB_QUALIFY_RC="$QUALIFY_RC"
    export STUB_VALIDATE_RC="$VALIDATE_RC"
    export STUB_ALEMBIC_RC="$ALEMBIC_RC"
    export STUB_SNAPSHOT_RESTORE_RC="$SNAPSHOT_RESTORE_RC"
    export STUB_BUILD_RC="${BUILD_RC:-0}"
    export STUB_UP_RC="${UP_RC:-0}"
    if [ "${RUN_XTRACE:-0}" = "1" ]; then
      bash -x "$CONTROLLER" "$@"
    else
      bash "$CONTROLLER" "$@"
    fi
  ) >"$SANDBOX/last-output" 2>&1
  OUT_RC=$?
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
}

s08_digest_repoint_reject() {
  new_sandbox
  run_controller candidate "$SHA1" >/dev/null 2>&1
  seed_deployed "$SHA1"
  seed_images "$SHA1"
  local map=$SANDBOX/ids.txt
  : >"$map"
  local svc
  for svc in api worker web; do
    printf 'novelforge-%s:%s sha256:stable\n' "$svc" "$SHA1" >>"$map"
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
  local src_db=nf_snap_src dst_db=nf_snap_dst
  su postgres -c "psql -qc \"DROP ROLE IF EXISTS nfcandidate\" >/dev/null" || true
  su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $src_db\" >/dev/null" || true
  su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $dst_db\" >/dev/null" || true
  su postgres -c "psql -qc \"CREATE ROLE nfcandidate LOGIN PASSWORD '$role_pw'\" >/dev/null"
  su postgres -c "createdb -O nfcandidate $src_db"
  # Old schema: migrate the worktree backend to the revision BEFORE head.
  local backend=$REPO_ROOT/backend
  local wsl_ip
  wsl_ip=$(hostname -I | awk '{print $1}')
  local url="postgresql+asyncpg://nfcandidate:$role_pw@$wsl_ip:5432/$src_db"
  local cmd_file=$SANDBOX/alembic.cmd
  make_alembic_cmd() { # $1=alembic args $2=url
    {
      printf '@echo off
'
      printf 'cd /d %s
' "$(wslpath -w "$backend")"
      printf 'set DATABASE_URL=%s
' "${2:-}"
      printf '%s -m alembic %s
' "$(wslpath -w "$WIN_PY")" "$1"
    } >"$cmd_file"
  }
  local parent_rev
  parent_rev=$( (cd "$backend" && "$WIN_PY" -m alembic history) 2>/dev/null | sed -n '1s/^\([0-9a-f]*\) ->.*/\1/p')
  [ -n "$parent_rev" ] || { assert_fail_record "cannot read alembic history"; return 0; }
  make_alembic_cmd "upgrade $parent_rev" "$url"
  cmd.exe /c "$(wslpath -w "$cmd_file")" >/dev/null 2>&1
  # old schema + one data row
  su postgres -c "psql -q -d $src_db -c \"INSERT INTO books (id, title) VALUES (gen_random_uuid(), 'snapshot-row')\"" >/dev/null
  su postgres -c "pg_dump --format=custom $src_db" >"$SANDBOX/snap.dump"
  # restore into the dst db through the controller's exec mechanism
  su postgres -c "createdb -O nfcandidate $dst_db"
  su postgres -c "pg_restore --dbname=$dst_db" <"$SANDBOX/snap.dump" >/dev/null 2>&1
  # migrate dst to head with the real alembic chain
  local dst_url="postgresql+asyncpg://nfcandidate:$role_pw@$wsl_ip:5432/$dst_db"
  make_alembic_cmd "upgrade head" "$dst_url"
  cmd.exe /c "$(wslpath -w "$cmd_file")" >/dev/null 2>&1
  local head_ver rows
  head_ver=$(su postgres -c "psql -tA -d $dst_db -c 'SELECT version_num FROM alembic_version'" 2>/dev/null)
  rows=$(su postgres -c "psql -tA -d $dst_db -c 'SELECT count(*) FROM books'" 2>/dev/null)
  assert_eq "$rows" "1" "snapshot data survived restore+migrate"
  assert_contains "$( (cd "$backend" && "$WIN_PY" -m alembic heads) 2>/dev/null)" "$head_ver"     "snapshot db migrated to alembic head"
  su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $src_db\" >/dev/null" || true
  su postgres -c "psql -qc \"DROP DATABASE IF EXISTS $dst_db\" >/dev/null" || true
  su postgres -c "psql -qc \"DROP ROLE IF EXISTS nfcandidate\" >/dev/null" || true
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
  # Lock contention: a held release lock must make the upgrader refuse (75).
  (
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    export NOVELFORGE_ROOT="$ROOT" NOVELFORGE_CONF="$CONF" NOVELFORGE_LOCK="$ROOT/lock/nf.lock"
    export NOVELFORGE_OPS_TARGET="$SANDBOX/usr/local/sbin/novelforge-release"
    export NOVELFORGE_OPS_WRAPPER_TARGET="$SANDBOX/usr/local/sbin/novelforge-ops"
    exec 9>"$ROOT/lock/nf.lock"
    flock 9
    bash "$UPGRADER" "$SHA2"
  ) >"$SANDBOX/lock-output" 2>&1
  assert_eq "$?" "75" "concurrent lock contention refused"
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
    CURRENT_FAILED=$((CURRENT_FAILED + 1))
    say "  assert-fail: migration failure still ran production up"
  fi
  assert_contains "$(docker_log)" "pg_dump" "backup happened before migration"
}

s13_health_failure_rollback() {
  new_sandbox
  seed_deployed "$SHA1"
  run_controller candidate "$SHA2" >/dev/null 2>&1
  seed_images "$SHA2"
  HEALTH_FAIL=1
  run_controller deploy "$SHA2" >/dev/null 2>&1
  assert_eq "$(current_release_sha)" "$SHA1" "symlink rolled back"
  assert_contains "$(docker_log)" "CMD=up ARGS=-d --remove-orphans" \
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
  STUB_PS_OUTPUT="$SANDBOX/ps.json" run_controller status >/dev/null 2>&1
  assert_contains "$(last_output)" '"mixed_release":false' "consistent provenance"
  assert_contains "$(last_output)" '"provenance_complete":true' "complete provenance"
  assert_contains "$(last_output)" '"unexpected_services":[]' "no unexpected services"
}

# ── precise scenario accounting (§10.2-14) ─────────────────────────────────

# Error signatures that mark a scenario's stderr as dirty (acceptance P0-D:
# unexpected bash/python errors must fail the scenario, never print through).
STDERR_ERROR_RE='no such file|unbound variable|unterminated|permission denied|integer expected|traceback|syntax error|command not found|ambiguous argument|invalid argument|not a valid|unexpected'

scenario() { # $1=name $2=fn
  CURRENT_TEST=$1
  SCENARIOS=$((SCENARIOS + 1))
  : >"$SANDBOX/assert-failures"
  : >"$SANDBOX/scenario.err"
  local rc=0
  (
    set -Eeuo pipefail
    trap 'echo "uncaught error at line $LINENO (rc=$?)" >&2' ERR
    "$2"
  ) >"$SANDBOX/last-output" 2>"$SANDBOX/scenario.err" || rc=$?

  local failures=0
  if [ -s "$SANDBOX/assert-failures" ]; then
    failures=$(grep -c . "$SANDBOX/assert-failures")
  fi
  local dirty=""
  if [ -s "$SANDBOX/scenario.err" ]     && grep -qiE "$STDERR_ERROR_RE" "$SANDBOX/scenario.err"; then
    dirty=yes
  fi

  if [ "$rc" -eq 0 ] && [ "$failures" = "0" ] && [ -z "$dirty" ]; then
    PASSED=$((PASSED + 1))
    say "PASS [$SCENARIOS/16] $CURRENT_TEST"
  else
    FAILED=$((FAILED + 1))
    say "FAIL [$SCENARIOS/16] $CURRENT_TEST (rc=$rc assertions=$failures dirty_stderr=$dirty)"
    sed -n '1,10p' "$SANDBOX/assert-failures" 2>/dev/null
    grep -iE "$STDERR_ERROR_RE" "$SANDBOX/scenario.err" 2>/dev/null | sed -n '1,4p'
    [ -f "$SANDBOX/last-output" ] && say "  last-output: $(head -c 300 "$SANDBOX/last-output")"
  fi
  if [ "${KEEP_FAILED:-0}" = "1" ]     && { [ "$rc" -ne 0 ] || [ "$failures" != "0" ] || [ -n "$dirty" ]; }; then
    say "  (sandbox kept: $SANDBOX)"
  else
    [ -n "$SANDBOX" ] && rm -rf "$SANDBOX"
  fi
  SANDBOX=""
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
scenario "migration failure leaves current and services unchanged" s12_migration_failure_no_change
scenario "health failure rolls back and reports structurally" s13_health_failure_rollback
scenario "status provenance variants incl. infra version and missing services" s14_status_provenance_variants

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
