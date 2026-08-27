#!/usr/bin/env bash
# Run once as root from the VPS provider console.
set -Eeuo pipefail
umask 077

usage() {
  echo "usage: $0 REPOSITORY_URL AUTHORIZED_PUBLIC_KEY_FILE" >&2
  exit 64
}

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 77; }
[ "$#" -eq 2 ] || usage
REPOSITORY_URL=$1
KEY_FILE=$2
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

[[ $REPOSITORY_URL =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]] \
  || { echo "repository must be an https GitHub URL" >&2; exit 64; }
[ -f "$KEY_FILE" ] || { echo "public key file missing" >&2; exit 66; }
PUBLIC_KEY=$(tr -d '\r\n' <"$KEY_FILE")
[[ $PUBLIC_KEY =~ ^ssh-ed25519[[:space:]][A-Za-z0-9+/=]+([[:space:]].*)?$ ]] \
  || { echo "only one ssh-ed25519 public key is accepted" >&2; exit 65; }

for command in git curl docker flock sudo sshd systemctl useradd usermod install visudo grep; do
  command -v "$command" >/dev/null || {
    echo "missing prerequisite: $command" >&2
    exit 69
  }
done

id novelops >/dev/null 2>&1 || useradd --create-home --shell /bin/sh novelops
# sshd executes ForcedCommand through the account shell, so it must be a real
# shell.  The key-level `restrict,command=...` options still prevent an
# interactive shell, forwarding, agents, PTYs, SCP and arbitrary commands.
usermod --shell /bin/sh novelops
install -d -m 0700 -o novelops -g novelops /home/novelops/.ssh
printf 'restrict,command="/usr/local/sbin/novelforge-ops" %s\n' "$PUBLIC_KEY" \
  >/home/novelops/.ssh/authorized_keys
chown novelops:novelops /home/novelops/.ssh/authorized_keys
chmod 0600 /home/novelops/.ssh/authorized_keys

install -d -m 0755 -o root -g root /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/90-novelforge-ops.conf <<'EOF'
Match User novelops
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    X11Forwarding no
EOF
if ! sshd -t; then
  rm -f /etc/ssh/sshd_config.d/90-novelforge-ops.conf
  echo "novelops SSH policy is not supported; configuration was removed" >&2
  exit 78
fi
EFFECTIVE_SSHD=$(sshd -T -C user=novelops,host=localhost,addr=127.0.0.1)
for expected in \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'permittty no' \
  'allowtcpforwarding no'; do
  if ! grep -qx "$expected" <<<"$EFFECTIVE_SSHD"; then
    rm -f /etc/ssh/sshd_config.d/90-novelforge-ops.conf
    echo "sshd did not apply required policy: $expected; configuration was removed" >&2
    exit 78
  fi
done
systemctl reload ssh 2>/dev/null || systemctl reload sshd

install -o root -g root -m 0755 "$SCRIPT_DIR/novelforge-ops" /usr/local/sbin/novelforge-ops
install -o root -g root -m 0755 "$SCRIPT_DIR/novelforge-release" /usr/local/sbin/novelforge-release
printf 'novelops ALL=(root) NOPASSWD: /usr/local/sbin/novelforge-release\n' \
  >/etc/sudoers.d/novelforge-ops
chmod 0440 /etc/sudoers.d/novelforge-ops
visudo -cf /etc/sudoers.d/novelforge-ops >/dev/null

install -d -m 0750 -o root -g root /srv/novelforge /srv/novelforge/releases
install -d -m 0750 -o root -g root /srv/novelforge/shared
install -d -m 0755 -o root -g root /srv/novelforge/shared/data
install -d -m 0750 -o 10001 -g 10001 \
  /srv/novelforge/shared/data/books \
  /srv/novelforge/shared/data/exports \
  /srv/novelforge/shared/data/references \
  /srv/novelforge/shared/data/imports
install -d -m 0750 -o root -g root /srv/novelforge/shared/data/backups
cat >/etc/novelforge-ops.conf <<EOF
REPOSITORY_URL=$REPOSITORY_URL
DEPLOY_BRANCH=main
HEALTH_URL=http://127.0.0.1/health/ready
EOF
chmod 0600 /etc/novelforge-ops.conf

cat >/etc/systemd/system/novelforge-healthcheck.service <<'EOF'
[Unit]
Description=NovelForge local health check and container self-heal
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/novelforge-release healthcheck
EOF

cat >/etc/systemd/system/novelforge-healthcheck.timer <<'EOF'
[Unit]
Description=Run NovelForge health check every two minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
RandomizedDelaySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now novelforge-healthcheck.timer

echo "novelops installed. NEXT: create /srv/novelforge/shared/.env (0600), then test 'ssh novel-hub status'."
echo "Root SSH was intentionally left unchanged; disable it only after the restricted key is verified."
