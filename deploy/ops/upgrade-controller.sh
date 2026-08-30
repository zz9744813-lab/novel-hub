#!/usr/bin/env bash
# Safe first-time activation of a new release controller (root console ONLY).
#
# Acceptance round-3 (P0-4 / §8): using a deploy to install the atomic
# controller would first run the OLD, non-atomic controller against production
# again. This script is the alternative bootstrap. It ONLY fetches/verifies
# git state, prepares the release worktree, syntax-checks the new controller,
# and transactionally installs the two controller files. It NEVER invokes
# docker compose, migrations, qualify, or switches the current symlink.
#
# usage: upgrade-controller.sh <40-hex commit on origin/main>
set -Eeuo pipefail
umask 027

ROOT=${NOVELFORGE_ROOT:-/srv/novelforge}
MIRROR="$ROOT/repo.git"
RELEASES="$ROOT/releases"
SHARED="$ROOT/shared"
CONF=${NOVELFORGE_CONF:-/etc/novelforge-ops.conf}
LOCK_FILE=${NOVELFORGE_LOCK:-/run/lock/novelforge-release.lock}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-main}
# Overridable install targets: behavior tests point these INSIDE the sandbox
# so the real install/mv never touches the host system paths.
OPS_TARGET=${NOVELFORGE_OPS_TARGET:-/usr/local/sbin/novelforge-release}
OPS_WRAPPER_TARGET=${NOVELFORGE_OPS_WRAPPER_TARGET:-/usr/local/sbin/novelforge-ops}

die() { echo "$2" >&2; exit "$1"; }

[[ $# -eq 1 ]] || { echo "usage: upgrade-controller.sh <40-hex commit on origin/main>" >&2; exit 64; }
SHA=$1
[[ $SHA =~ ^[0-9a-f]{40}$ ]] || { echo "invalid sha" >&2; exit 64; }
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 77; }

[ -r "$CONF" ] || { echo "missing $CONF" >&2; exit 78; }
# shellcheck source=/dev/null
source "$CONF"
: "${REPOSITORY_URL:?missing REPOSITORY_URL}"

# §8.1: share the release controller lock — the upgrader touches the same Git
# mirror/worktree state as candidate/deploy/healthcheck and must not race
# them.
exec 9>"$LOCK_FILE"
flock -w 5 9 || { echo '{"ok":false,"error":"release_busy"}'; exit 75; }

before_release=$(sha256sum "$OPS_TARGET" 2>/dev/null | cut -d' ' -f1 || true)
before_wrapper=$(sha256sum "$OPS_WRAPPER_TARGET" 2>/dev/null | cut -d' ' -f1 || true)

# 1) fetch + verify the commit really is on origin/main.
if [ ! -d "$MIRROR" ]; then
  git clone --mirror "$REPOSITORY_URL" "$MIRROR"
fi
git --git-dir="$MIRROR" remote set-url origin "$REPOSITORY_URL"
git --git-dir="$MIRROR" fetch --prune origin "+refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"
git --git-dir="$MIRROR" rev-parse --verify "$SHA^{commit}" >/dev/null
git --git-dir="$MIRROR" merge-base --is-ancestor "$SHA" "refs/remotes/origin/$DEPLOY_BRANCH" \
  || die 65 "sha is not on origin/$DEPLOY_BRANCH"

# 2) prepare the release worktree (git + symlinks only; no docker, no gates).
release="$RELEASES/$SHA"
if [ ! -f "$release/.git" ]; then
  [ ! -e "$release" ] || die 73 "invalid pre-existing release path"
  git --git-dir="$MIRROR" worktree add --detach "$release" "$SHA" >&2
fi
[ -f "$SHARED/.env" ] || die 78 "missing $SHARED/.env"
[ -f "$release/deploy/ops/novelforge-release" ] || die 73 "controller missing in release"
[ -f "$release/deploy/ops/novelforge-ops" ] || die 73 "forced-command wrapper missing in release"
rm -f "$release/deploy/.env"
ln -s "$SHARED/.env" "$release/deploy/.env"
install -d -m 0750 "$SHARED/release-tags"
if [ ! -f "$SHARED/release-tags/$SHA.env" ]; then
  printf 'RELEASE_TAG=%s\n' "$SHA" >"$SHARED/release-tags/$SHA.env.tmp"
  chmod 0640 "$SHARED/release-tags/$SHA.env.tmp"
  mv -f "$SHARED/release-tags/$SHA.env.tmp" "$SHARED/release-tags/$SHA.env"
fi
if [ -e "$release/data" ] && [ ! -L "$release/data" ]; then
  die 73 "release contains a non-symlink data path"
fi
ln -sfn "$SHARED/data" "$release/data"

# 3) syntax-check both controller files before replacing anything.
bash -n "$release/deploy/ops/novelforge-release" \
  || die 65 "new controller fails bash -n"
bash -n "$release/deploy/ops/novelforge-ops" \
  || die 65 "new forced-command wrapper fails bash -n"
grep -q 'novelforge-release' "$release/deploy/ops/novelforge-ops" \
  || die 65 "forced-command wrapper does not reference the controller"

# 4) transactional two-file install (§8.2/§8.3): stage both files, verify
# their hashes against the release sources, then mv them into place. If the
# second mv fails, the first is restored from its staged copy so the pair is
# never left mixed old/new.
install -d -m 0755 "$(dirname "$OPS_TARGET")" "$(dirname "$OPS_WRAPPER_TARGET")"
stage_release=$(mktemp "$OPS_TARGET.new.XXXXXX")
stage_wrapper=$(mktemp "$OPS_WRAPPER_TARGET.new.XXXXXX")
restore_release=""
cleanup_stage() {
  rm -f "$stage_release" "$stage_wrapper"
}
trap cleanup_stage EXIT

install -o root -g root -m 0755 "$release/deploy/ops/novelforge-release" "$stage_release"
install -o root -g root -m 0755 "$release/deploy/ops/novelforge-ops" "$stage_wrapper"

staged_release_hash=$(sha256sum "$stage_release" | cut -d' ' -f1)
staged_wrapper_hash=$(sha256sum "$stage_wrapper" | cut -d' ' -f1)
source_release_hash=$(sha256sum "$release/deploy/ops/novelforge-release" | cut -d' ' -f1)
source_wrapper_hash=$(sha256sum "$release/deploy/ops/novelforge-ops" | cut -d' ' -f1)
[ "$staged_release_hash" = "$source_release_hash" ] \
  || die 70 "staged controller hash mismatch"
[ "$staged_wrapper_hash" = "$source_wrapper_hash" ] \
  || die 70 "staged wrapper hash mismatch"

if [ -f "$OPS_TARGET" ]; then
  cp -f "$OPS_TARGET" "$OPS_TARGET.prev"
  restore_release=1
fi
install_ok=false
if mv -f "$stage_release" "$OPS_TARGET"; then
  if mv -f "$stage_wrapper" "$OPS_WRAPPER_TARGET"; then
    install_ok=true
  else
    [ "$restore_release" = "1" ] && mv -f "$OPS_TARGET.prev" "$OPS_TARGET"
  fi
fi

after_release=$(sha256sum "$OPS_TARGET" 2>/dev/null | cut -d' ' -f1 || true)
after_wrapper=$(sha256sum "$OPS_WRAPPER_TARGET" 2>/dev/null | cut -d' ' -f1 || true)

if [ "$install_ok" != "true" ] \
  || [ "$after_release" != "$source_release_hash" ] \
  || [ "$after_wrapper" != "$source_wrapper_hash" ]; then
  echo '{"ok":false,"action":"upgrade-controller","installed":false}' >&2
  exit 70
fi

printf '{"ok":true,"action":"upgrade-controller","sha":"%s","before_release_sha256":"%s","after_release_sha256":"%s","before_wrapper_sha256":"%s","after_wrapper_sha256":"%s","source_release_sha256":"%s","source_wrapper_sha256":"%s"}\n' \
  "$SHA" "${before_release:-null}" "$after_release" \
  "${before_wrapper:-null}" "$after_wrapper" \
  "$source_release_hash" "$source_wrapper_hash"
