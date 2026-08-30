#!/usr/bin/env bash
# Safe first-time activation of a new release controller (root console ONLY).
#
# Acceptance §6 (P0-4): using a deploy to install the atomic controller would
# first run the OLD, non-atomic controller against production again. This
# script is the alternative bootstrap: it ONLY fetches/verifies git state,
# prepares the release worktree, syntax-checks the new controller, and atomi-
# cally installs the two controller files. It NEVER invokes docker compose,
# migrations, qualify, or switches the current symlink.
#
# usage: upgrade-controller.sh <40-hex commit on origin/main>
set -Eeuo pipefail
umask 027

ROOT=${NOVELFORGE_ROOT:-/srv/novelforge}
MIRROR="$ROOT/repo.git"
RELEASES="$ROOT/releases"
SHARED="$ROOT/shared"
CONF=${NOVELFORGE_CONF:-/etc/novelforge-ops.conf}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-main}
REPOSITORY_URL_DEFAULT=""
OPS_TARGET=/usr/local/sbin/novelforge-release
OPS_WRAPPER_TARGET=/usr/local/sbin/novelforge-ops

[[ $# -eq 1 ]] || { echo "usage: upgrade-controller.sh <40-hex commit on origin/main>" >&2; exit 64; }
SHA=$1
[[ $SHA =~ ^[0-9a-f]{40}$ ]] || { echo "invalid sha" >&2; exit 64; }
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 77; }

[ -r "$CONF" ] || { echo "missing $CONF" >&2; exit 78; }
# shellcheck source=/dev/null
source "$CONF"
: "${REPOSITORY_URL:?missing REPOSITORY_URL}"

release_env_tag_file() { # $1=release path -> per-release RELEASE_TAG file
  local sha=${1##*/}
  local dir="$SHARED/release-tags"
  install -d -m 0750 "$dir"
  if [ ! -f "$dir/$sha.env" ]; then
    printf 'RELEASE_TAG=%s\n' "$sha" >"$dir/$sha.env.tmp"
    chmod 0640 "$dir/$sha.env.tmp"
    mv -f "$dir/$sha.env.tmp" "$dir/$sha.env"
  fi
}

before=$(sha256sum "$OPS_TARGET" 2>/dev/null | cut -d' ' -f1 || true)

# 1) fetch + verify the commit really is on origin/main.
if [ ! -d "$MIRROR" ]; then
  git clone --mirror "$REPOSITORY_URL" "$MIRROR"
fi
git --git-dir="$MIRROR" remote set-url origin "$REPOSITORY_URL"
git --git-dir="$MIRROR" fetch --prune origin "+refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"
git --git-dir="$MIRROR" rev-parse --verify "$SHA^{commit}" >/dev/null
git --git-dir="$MIRROR" merge-base --is-ancestor "$SHA" "refs/remotes/origin/$DEPLOY_BRANCH" \
  || { echo "sha is not on origin/$DEPLOY_BRANCH" >&2; exit 65; }

# 2) prepare the release worktree (git + symlinks only; no docker, no gates).
release="$RELEASES/$SHA"
if [ ! -f "$release/.git" ]; then
  [ ! -e "$release" ] || { echo "invalid pre-existing release path" >&2; exit 73; }
  git --git-dir="$MIRROR" worktree add --detach "$release" "$SHA" >&2
fi
[ -f "$SHARED/.env" ] || { echo "missing $SHARED/.env" >&2; exit 78; }
[ -f "$release/deploy/ops/novelforge-release" ] || { echo "controller missing in release" >&2; exit 73; }
rm -f "$release/deploy/.env"
ln -s "$SHARED/.env" "$release/deploy/.env"
release_env_tag_file "$release"
if [ -e "$release/data" ] && [ ! -L "$release/data" ]; then
  echo "release contains a non-symlink data path" >&2
  exit 73
fi
ln -sfn "$SHARED/data" "$release/data"

# 3) syntax-check both controller files before replacing anything.
bash -n "$release/deploy/ops/novelforge-release" \
  || { echo "new controller fails bash -n" >&2; exit 65; }
bash -n "$release/deploy/ops/novelforge-ops" \
  || { echo "new forced-command wrapper fails bash -n" >&2; exit 65; }
grep -q 'novelforge-release' "$release/deploy/ops/novelforge-ops" \
  || { echo "forced-command wrapper does not reference the controller" >&2; exit 65; }

# 4) atomic install + verification. ZERO docker/migration/qualify/switch calls.
tmp_release=$(mktemp "$OPS_TARGET.new.XXXXXX")
tmp_wrapper=$(mktemp "$OPS_WRAPPER_TARGET.new.XXXXXX")
install -o root -g root -m 0755 "$release/deploy/ops/novelforge-release" "$tmp_release"
install -o root -g root -m 0755 "$release/deploy/ops/novelforge-ops" "$tmp_wrapper"
mv -f "$tmp_release" "$OPS_TARGET"
mv -f "$tmp_wrapper" "$OPS_WRAPPER_TARGET"

after=$(sha256sum "$OPS_TARGET" | cut -d' ' -f1)

printf '{"ok":true,"action":"upgrade-controller","sha":"%s","before_sha256":"%s","after_sha256":"%s"}\n' \
  "$SHA" "${before:-null}" "$after"
