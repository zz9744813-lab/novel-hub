#!/usr/bin/env bash
# Behavior tests for deploy/ops/novelforge-release (deploy atomicity rework).
#
# Runs the REAL controller against a stubbed docker/curl/install environment
# and a real git mirror, asserting the ops-rework §10 contract:
#   - candidate self-prepares and gates under the isolated project without
#     touching any production container;
#   - a failed/missing/stale candidate blocks deploy before backup/migration;
#   - deploy is backup -> one-off migration -> switch -> health -> rollback;
#   - status reports mixed release provenance.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
CONTROLLER="$REPO_ROOT/deploy/ops/novelforge-release"

PASS=0
FAIL=0
CURRENT_TEST=""

# Scriptable stub behaviour (defaults; scenarios override before run_controller)
QUALIFY_RC=0
VALIDATE_RC=0
QUALIFY_PAYLOAD=""
VALIDATE_PAYLOAD=""
IMAGES_PRESENT=""
HEALTH_FAIL=0

SANDBOX=""
SRC=""
SHA1=""
SHA2=""
ROOT=""
SHARED=""
CONF=""

say() { printf '%s\n' "$*"; }
pass() { PASS=$((PASS + 1)); say "PASS: $CURRENT_TEST"; }
fail() {
  FAIL=$((FAIL + 1))
  say "FAIL: $CURRENT_TEST -- $* (rc=$OUT_RC)"
  if [ -f "$SANDBOX/last-output" ]; then
    say "--- last-output:"
    sed -n '1,6p' "$SANDBOX/last-output"
  fi
  if [ -f "$SANDBOX/docker-calls.log" ]; then
    say "--- docker-calls.log:"
    sed -n '1,8p' "$SANDBOX/docker-calls.log"
  fi
}
assert_eq() { # got expected label
  if [ "$1" = "$2" ]; then return 0; fi
  fail "$3 (got: '$1', want: '$2')"
  return 1
}
assert_contains() { # file needle label
  if grep -qF -- "$2" "$1"; then return 0; fi
  fail "$3 (missing '$2' in $1)"
  return 1
}

cleanup() {
  [ -n "$SANDBOX" ] && rm -rf "$SANDBOX"
  return 0
}
trap cleanup EXIT

begin_test() {
  CURRENT_TEST=$1
  SANDBOX=$(mktemp -d)
  ROOT=$SANDBOX/root
  SHARED=$ROOT/shared
  mkdir -p "$ROOT/lock" "$SHARED/data/backups" "$SHARED/candidates" "$ROOT/releases"
  printf 'POSTGRES_USER=novelforge\nPOSTGRES_PASSWORD=secret\nPOSTGRES_DB=novelforge\n' \
    >"$SHARED/.env"
  chmod 0640 "$SHARED/.env"

  SRC=$SANDBOX/src
  git init -q -b main "$SRC"
  git -C "$SRC" config core.autocrlf false
  mkdir -p "$SRC/deploy/ops" "$SRC/backend"
  cp -f "$CONTROLLER" "$SRC/deploy/ops/novelforge-release"
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

  export MSYS=winsymlinks:nativestrict
  CONF=$SANDBOX/novelforge-ops.conf
  {
    printf 'REPOSITORY_URL=%s\n' "$SRC"
    printf 'HEALTH_URL=http://127.0.0.1:1/health/ready\n'
  } >"$CONF"

  QUALIFY_RC=0
  VALIDATE_RC=0
  ALEMBIC_RC=0
  QUALIFY_PAYLOAD=$(gate_payload true)
  VALIDATE_PAYLOAD=$(gate_payload true)
  IMAGES_PRESENT=""
  HEALTH_FAIL=0
  return 0
}

gate_payload() { # $1 = passed(true|false) -> canned production_pack payload file
  # NOTE: split assignments -- `local a=$1 b=$a` expands $a before assigning.
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
  local svc
  for svc in api worker web; do
    case ",$IMAGES_PRESENT," in
      *",novelforge-$svc:$sha,"*) ;;
      *) IMAGES_PRESENT="novelforge-$svc:$sha,$IMAGES_PRESENT" ;;
    esac
  done
}

seed_images() { # $1 = sha: mark that release's images as already built
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
  printf 'INVOKED=%s
' "$*" >>"$log"
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
    [ -n "$QUALIFY_PAYLOAD" ] && export STUB_QUALIFY_OUTPUT="$QUALIFY_PAYLOAD"
    [ -n "$VALIDATE_PAYLOAD" ] && export STUB_VALIDATE_OUTPUT="$VALIDATE_PAYLOAD"
    export STUB_QUALIFY_RC="$QUALIFY_RC"
    export STUB_VALIDATE_RC="$VALIDATE_RC"
    export STUB_ALEMBIC_RC="${ALEMBIC_RC:-0}"
    export GIT_CONFIG_GLOBAL="$SANDBOX/gitconfig"
    : >"$SANDBOX/gitconfig"
    printf '[core]
	autocrlf = false
' >>"$SANDBOX/gitconfig"
    export MSYS=winsymlinks:nativestrict
    bash "$CONTROLLER" "$@"
  ) >"$SANDBOX/last-output" 2>&1
  OUT_RC=$?
  return 0
}

last_output() { printf '%s\n' "$SANDBOX/last-output"; }
docker_log() { printf '%s\n' "$SANDBOX/docker-calls.log"; }

prod_mutations() { # calls that would touch/recreate running production containers
  grep -cE '^PROJECT=prod CMD=(up|build|run|down)' "$(docker_log)" 2>/dev/null \
    || true
}

current_release_sha() {
  local link
  link=$(readlink "$ROOT/current") || {
    printf ''
    return 0
  }
  basename "$link"
}

# ── scenario 1 ──────────────────────────────────────────────────────────────
begin_test "candidate auto-prepares release and runs isolated gates"
run_controller candidate "$SHA1"
assert_eq "$OUT_RC" "0" "candidate exit code"
assert_contains "$(docker_log)" "PROJECT=candidate CMD=build" \
  "candidate builds under isolated project"
assert_contains "$(docker_log)" "PROJECT=candidate CMD=up" \
  "candidate starts isolated throwaway db"
assert_contains "$(docker_log)" "ONEOFF_CMD= alembic upgrade head" \
  "candidate migrates the throwaway db"
if [ "$(prod_mutations)" = "0" ]; then pass; else
  fail "candidate touched production compose (mutations=$(prod_mutations))"
fi
assert_contains "$SHARED/candidates/$SHA1.envelope" "passed=true" \
  "candidate envelope passed"
assert_contains "$SHARED/candidates/$SHA1.envelope" "sha=$SHA1" \
  "candidate envelope sha binding"

# ── scenario 2 ──────────────────────────────────────────────────────────────
begin_test "candidate model-gate failure leaves production untouched"
QUALIFY_RC=1
QUALIFY_PAYLOAD=$(gate_payload false)
run_controller candidate "$SHA2"
assert_eq "$OUT_RC" "71" "candidate exit code on failed model gate"
assert_contains "$SHARED/candidates/$SHA2.envelope" "passed=false" \
  "failed candidate records passed=false"
if [ "$(prod_mutations)" = "0" ]; then pass; else
  fail "failed candidate touched production compose (mutations=$(prod_mutations))"
fi
run_controller deploy "$SHA2"
assert_eq "$OUT_RC" "71" "deploy refuses failed candidate"
assert_contains "$(last_output)" "candidate gate FAILED" "refusal message"
if [ -e "$ROOT/current" ]; then
  fail "deploy of failed candidate switched symlink"
else
  pass
fi

# ── scenario 3 ──────────────────────────────────────────────────────────────
begin_test "deploy refuses missing candidate gate result"
run_controller deploy "$SHA1"
assert_eq "$OUT_RC" "71" "deploy exit code"
assert_contains "$(last_output)" "candidate gate result missing" "missing-candidate error"
if [ "$(prod_mutations)" = "0" ]; then pass; else
  fail "deploy without candidate mutated production (mutations=$(prod_mutations))"
fi

# ── scenario 4 ──────────────────────────────────────────────────────────────
begin_test "deploy refuses stale candidate gate result"
run_controller candidate "$SHA1" >/dev/null 2>&1
sed -i "s/^created=.*/created=$(( $(date -u +%s) - 90000 ))/" \
  "$SHARED/candidates/$SHA1.envelope"
run_controller deploy "$SHA1"
assert_eq "$OUT_RC" "71" "deploy exit code on stale candidate"
assert_contains "$(last_output)" "stale" "stale-candidate error"

# ── scenario 5 ──────────────────────────────────────────────────────────────
begin_test "deploy consumes candidate: backup, one-off migration, switch, health"
run_controller candidate "$SHA1" >/dev/null 2>&1
seed_deployed "$SHA1"
run_controller candidate "$SHA2" >/dev/null 2>&1
seed_images "$SHA2"
run_controller deploy "$SHA2"
assert_eq "$OUT_RC" "0" "deploy exit code"
assert_contains "$(docker_log)" "pg_dump" "deploy backs up database"
assert_contains "$(docker_log)" "PROJECT=prod CMD=run ARGS=--rm --no-deps api" \
  "deploy migration is a one-off container"
assert_contains "$(docker_log)" "ONEOFF_CMD= alembic upgrade head" \
  "deploy runs alembic against production db"
assert_contains "$(docker_log)" "PROJECT=prod CMD=up ARGS=-d --remove-orphans" \
  "deploy starts services after switch"
assert_eq "$(current_release_sha)" "$SHA2" "current symlink switched"
assert_contains "$SHARED/DEPLOYED_SHA" "$SHA2" "deployed sha recorded"
if grep -q '^PROJECT=prod CMD=build' "$(docker_log)"; then
  fail "deploy rebuilt images instead of consuming candidate images"
else
  pass
fi

# ── scenario 6 ──────────────────────────────────────────────────────────────
begin_test "migration failure aborts before switch"
seed_deployed "$SHA1"
run_controller candidate "$SHA2" >/dev/null 2>&1
seed_images "$SHA2"
: >"$(docker_log)"
ALEMBIC_RC=1
run_controller deploy "$SHA2"
assert_eq "$OUT_RC" "1" "deploy exit code on migration failure"
assert_eq "$(current_release_sha)" "$SHA1" "symlink unchanged"
if grep -q 'PROJECT=prod CMD=up' "$(docker_log)"; then
  fail "migration failure still ran production up"
else
  pass
fi

# ── scenario 7 ──────────────────────────────────────────────────────────────
begin_test "health failure after switch rolls back to previous release"
seed_deployed "$SHA1"
run_controller candidate "$SHA2" >/dev/null 2>&1
seed_images "$SHA2"
: >"$(docker_log)"
HEALTH_FAIL=1
run_controller deploy "$SHA2"
assert_eq "$(current_release_sha)" "$SHA1" "symlink rolled back"
assert_contains "$(docker_log)" "PROJECT=prod CMD=up" \
  "rollback restarted previous services"

# ── scenario 8 ──────────────────────────────────────────────────────────────
begin_test "status reports mixed release provenance"
seed_deployed "$SHA1"
cat >"$SANDBOX/ps.json" <<EOF
{"Name":"novelforge-api-1","Service":"api","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy,com.docker.compose.project=novelforge"}
{"Name":"novelforge-postgres-1","Service":"postgres","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA2/deploy,com.docker.compose.project=novelforge"}
EOF
STUB_PS_OUTPUT="$SANDBOX/ps.json" run_controller status
assert_contains "$(last_output)" '"mixed_release":true' "mixed provenance detected"
assert_contains "$(last_output)" "\"api\":\"$SHA1\"" "api provenance"
assert_contains "$(last_output)" "\"postgres\":\"$SHA2\"" "postgres provenance"

# ── scenario 9 ──────────────────────────────────────────────────────────────
begin_test "status reports consistent provenance"
seed_deployed "$SHA1"
cat >"$SANDBOX/ps.json" <<EOF
{"Name":"novelforge-api-1","Service":"api","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
{"Name":"novelforge-postgres-1","Service":"postgres","Labels":"com.docker.compose.project.working_dir=$ROOT/releases/$SHA1/deploy"}
EOF
STUB_PS_OUTPUT="$SANDBOX/ps.json" run_controller status
assert_contains "$(last_output)" '"mixed_release":false' "consistent provenance"

# ── scenario 10 ─────────────────────────────────────────────────────────────
begin_test "deploy refuses when candidate images are missing"
seed_deployed "$SHA1"
run_controller candidate "$SHA2" >/dev/null 2>&1
seed_images "$SHA2"
IMAGES_PRESENT=""
run_controller deploy "$SHA2"
assert_contains "$(last_output)" "missing; run 'candidate" "image-missing error"
if grep -q 'pg_dump' "$(docker_log)"; then
  fail "deploy attempted backup despite missing images"
else
  pass
fi

say ""
say "results: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
