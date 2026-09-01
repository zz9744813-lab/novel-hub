#!/usr/bin/env bash
# noVNC-safe first bootstrap. Invoke through one short curl-and-run line.
set -Eeuo pipefail
umask 077

readonly REPOSITORY_URL=https://github.com/zz9744813-lab/novel-hub.git
readonly RAW_BASE=https://raw.githubusercontent.com/zz9744813-lab/novel-hub
# The ops commit to bootstrap MUST be provided explicitly (40-hex, on main).
# A hardcoded commit here would silently reinstall stale controllers.
readonly OPS_COMMIT=${OPS_COMMIT:?set OPS_COMMIT to the 40-hex main commit whose deploy/ops controllers should be installed}
[[ $OPS_COMMIT =~ ^[0-9a-f]{40}$ ]] || { echo "OPS_COMMIT must be 40 lowercase hex characters" >&2; exit 64; }
# This is a public key, not a credential. Keeping the dedicated operator key
# here removes the last long, mixed-case argument from unreliable noVNC paste.
readonly DEFAULT_KEY_BODY=AAAAC3NzaC1lZDI1NTE5AAAAIJgT3xuYobfW7EuxqF8exL8bgfbGKCSa/9ORDivDzZjM

usage() {
  echo "usage: bootstrap-console.sh [ED25519_PUBLIC_KEY_BODY]" >&2
  exit 64
}

[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 77; }
[[ $# -le 1 ]] || usage
KEY_BODY=${1:-$DEFAULT_KEY_BODY}
[[ $KEY_BODY =~ ^[A-Za-z0-9+/]+={0,2}$ ]] \
  || { echo "invalid ed25519 public key body" >&2; exit 65; }

for command in bash chown chmod curl install mktemp rm ssh-keygen; do
  command -v "$command" >/dev/null || {
    echo "missing prerequisite: $command" >&2
    exit 69
  }
done

BOOTSTRAP_DIR=$(mktemp -d)
cleanup() {
  rm -rf -- "$BOOTSTRAP_DIR"
}
trap cleanup EXIT

for file in \
  bootstrap-novelops.sh \
  enable-management-ssh.sh \
  novelforge-ops \
  novelforge-release; do
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
    --connect-timeout 15 --max-time 120 --retry 3 --retry-all-errors \
    "$RAW_BASE/$OPS_COMMIT/deploy/ops/$file" \
    --output "$BOOTSTRAP_DIR/$file"
done
printf 'ssh-ed25519 %s novelforge-ops@107.172.138.14\n' "$KEY_BODY" \
  >"$BOOTSTRAP_DIR/novelforge_ops.pub"
ssh-keygen -l -f "$BOOTSTRAP_DIR/novelforge_ops.pub" >/dev/null \
  || { echo "invalid ed25519 public key" >&2; exit 65; }

bash "$BOOTSTRAP_DIR/bootstrap-novelops.sh" \
  "$REPOSITORY_URL" "$BOOTSTRAP_DIR/novelforge_ops.pub"
bash "$BOOTSTRAP_DIR/enable-management-ssh.sh" 22022

readonly ENV_TARGET=/srv/novelforge/shared/.env
if [[ -f $ENV_TARGET ]]; then
  chown root:root "$ENV_TARGET"
  chmod 0600 "$ENV_TARGET"
else
  ENV_SOURCE=
  for candidate in /root/novelforge/deploy/.env /root/novelforge/.env; do
    if [[ -f $candidate ]]; then
      ENV_SOURCE=$candidate
      break
    fi
  done
  [[ -n $ENV_SOURCE ]] || {
    echo "existing NovelForge .env not found in the supported locations" >&2
    exit 66
  }
  install -o root -g root -m 0600 "$ENV_SOURCE" "$ENV_TARGET"
fi

echo "NOVELFORGE_BOOTSTRAP_OK"
