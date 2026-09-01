#!/usr/bin/env bash
# Add a key-only, forced-command SSH listener without changing the primary SSH service.
set -Eeuo pipefail
umask 077

readonly PORT=${1:-22022}
readonly SERVICE=novelforge-sshd-alt.service

if [[ ${NOVELFORGE_SSH_TESTING:-0} == 1 ]]; then
  readonly ROOT_PREFIX=${NOVELFORGE_SSH_TEST_ROOT:?set NOVELFORGE_SSH_TEST_ROOT}
else
  readonly ROOT_PREFIX=
fi

readonly UNIT_DIR="$ROOT_PREFIX/etc/systemd/system"
readonly UNIT_PATH="$UNIT_DIR/$SERVICE"
readonly AUTHORIZED_KEYS="$ROOT_PREFIX/home/novelops/.ssh/authorized_keys"
readonly OPS_COMMAND="$ROOT_PREFIX/usr/local/sbin/novelforge-ops"

fail() {
  printf 'management SSH setup failed: %s\n' "$1" >&2
  exit "${2:-1}"
}

[[ $(id -u) -eq 0 ]] || fail 'run as root' 77
[[ $PORT =~ ^[0-9]+$ ]] || fail 'port must be numeric' 64
(( PORT >= 1024 && PORT <= 65535 )) || fail 'port must be between 1024 and 65535' 64

for command in awk cp grep id install mktemp rm ss sshd systemctl; do
  command -v "$command" >/dev/null || fail "missing prerequisite: $command" 69
done
readonly SSHD_BIN=$(command -v sshd)

id novelops >/dev/null 2>&1 || fail 'restricted novelops account is not installed' 66
[[ -x $OPS_COMMAND ]] || fail 'forced-command controller is not installed' 66
[[ -s $AUTHORIZED_KEYS ]] || fail 'novelops authorized_keys is missing' 66
grep -Eq '^restrict,command="/usr/local/sbin/novelforge-ops"[[:space:]]+ssh-ed25519[[:space:]]' \
  "$AUTHORIZED_KEYS" || fail 'novelops key is not restricted to novelforge-ops' 78

public_listener_exists() {
  ss -H -ltn "sport = :$PORT" 2>/dev/null \
    | awk -v suffix=":$PORT" '$4 == "0.0.0.0" suffix || $4 == "*" suffix || $4 == "[::]" suffix { found=1 } END { exit !found }'
}

if public_listener_exists; then
  if ! systemctl is-active --quiet "$SERVICE" \
    || [[ ! -f $UNIT_PATH ]] \
    || ! grep -Fq -- "-p $PORT" "$UNIT_PATH"; then
    fail "port $PORT is already owned by another listener" 73
  fi
fi

readonly SSHD_RESTRICTIONS=(
  -p "$PORT"
  -o "ListenAddress=0.0.0.0:$PORT"
  -o "PidFile=/run/novelforge-sshd-alt-$PORT.pid"
  -o AllowUsers=novelops
  -o PermitRootLogin=no
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o PubkeyAuthentication=yes
  -o PermitTTY=no
  -o AllowTcpForwarding=no
  -o AllowAgentForwarding=no
  -o X11Forwarding=no
  -o PermitTunnel=no
  -o GatewayPorts=no
)

"$SSHD_BIN" -t || fail 'the existing sshd configuration is invalid' 78
EFFECTIVE_SSHD=$(
  "$SSHD_BIN" -T \
    -C "user=novelops,host=localhost,addr=127.0.0.1,laddr=127.0.0.1,lport=$PORT" \
    "${SSHD_RESTRICTIONS[@]}"
) || fail 'the restricted listener configuration is invalid' 78

for expected in \
  "port $PORT" \
  "listenaddress 0.0.0.0:$PORT" \
  'allowusers novelops' \
  'permitrootlogin no' \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'pubkeyauthentication yes' \
  'permittty no' \
  'allowtcpforwarding no' \
  'allowagentforwarding no' \
  'x11forwarding no' \
  'permittunnel no' \
  'gatewayports no'; do
  grep -qx "$expected" <<<"$EFFECTIVE_SSHD" \
    || fail "sshd did not apply required policy: $expected" 78
done

WORK_DIR=$(mktemp -d)
readonly WORK_DIR
readonly UNIT_NEW="$WORK_DIR/$SERVICE"
readonly UNIT_OLD="$WORK_DIR/$SERVICE.old"
HAD_UNIT=0
WAS_ACTIVE=0
WAS_ENABLED=0
CHANGED=0
FIREWALL_ADDED=0
COMMITTED=0

[[ ! -f $UNIT_PATH ]] || {
  HAD_UNIT=1
  cp -a -- "$UNIT_PATH" "$UNIT_OLD"
}
systemctl is-active --quiet "$SERVICE" && WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$SERVICE" && WAS_ENABLED=1 || true

rollback() {
  local exit_code=$?
  if (( COMMITTED == 0 && CHANGED == 1 )); then
    set +e
    if (( FIREWALL_ADDED == 1 )); then
      ufw --force delete allow "$PORT/tcp" >/dev/null 2>&1
    fi
    if (( HAD_UNIT == 1 )); then
      install -o root -g root -m 0644 "$UNIT_OLD" "$UNIT_PATH"
      systemctl daemon-reload >/dev/null 2>&1
      if (( WAS_ENABLED == 1 )); then systemctl enable "$SERVICE" >/dev/null 2>&1; else systemctl disable "$SERVICE" >/dev/null 2>&1; fi
      if (( WAS_ACTIVE == 1 )); then systemctl restart "$SERVICE" >/dev/null 2>&1; else systemctl stop "$SERVICE" >/dev/null 2>&1; fi
    else
      systemctl stop "$SERVICE" >/dev/null 2>&1
      systemctl disable "$SERVICE" >/dev/null 2>&1
      rm -f -- "$UNIT_PATH"
      systemctl daemon-reload >/dev/null 2>&1
      systemctl reset-failed "$SERVICE" >/dev/null 2>&1
    fi
  fi
  rm -rf -- "$WORK_DIR"
  exit "$exit_code"
}
trap rollback EXIT

cat >"$UNIT_NEW" <<EOF
[Unit]
Description=NovelForge restricted management SSH listener on port $PORT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$SSHD_BIN -D -e -p $PORT -o ListenAddress=0.0.0.0:$PORT -o PidFile=/run/novelforge-sshd-alt-$PORT.pid -o AllowUsers=novelops -o PermitRootLogin=no -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o PubkeyAuthentication=yes -o PermitTTY=no -o AllowTcpForwarding=no -o AllowAgentForwarding=no -o X11Forwarding=no -o PermitTunnel=no -o GatewayPorts=no
ExecReload=/bin/kill -HUP \$MAINPID
KillMode=process
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

install -d -o root -g root -m 0755 "$UNIT_DIR"
install -o root -g root -m 0644 "$UNIT_NEW" "$UNIT_PATH"
CHANGED=1
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE" || fail 'alternate sshd service did not become active' 70
public_listener_exists || fail "alternate sshd did not listen publicly on port $PORT" 70

FIREWALL=not-managed
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qx 'Status: active'; then
  FIREWALL=existing
  if ! ufw status | awk -v port="$PORT/tcp" '$1 == port { found=1 } END { exit !found }'; then
    ufw allow "$PORT/tcp" comment 'NovelForge restricted management SSH'
    FIREWALL_ADDED=1
    FIREWALL=opened
  fi
fi

COMMITTED=1
printf '{"ok":true,"action":"enable-management-ssh","port":%d,"service":"%s","firewall":"%s","primary_ssh_unchanged":true,"root_login_on_management_port":false}\n' \
  "$PORT" "$SERVICE" "$FIREWALL"
