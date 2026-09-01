#!/usr/bin/env bash
set -Eeuo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$HERE/../../.." && pwd)
SCRIPT="$REPO_ROOT/deploy/ops/enable-management-ssh.sh"
PASS=0
FAIL=0
SANDBOX=

cleanup() {
  [[ -z ${SANDBOX:-} ]] || rm -rf -- "$SANDBOX"
}
trap cleanup EXIT

new_sandbox() {
  cleanup
  SANDBOX=$(mktemp -d)
  mkdir -p "$SANDBOX/bin" "$SANDBOX/root/home/novelops/.ssh" \
    "$SANDBOX/root/usr/local/sbin" "$SANDBOX/root/etc/systemd/system" "$SANDBOX/state"
  printf '%s\n' 'restrict,command="/usr/local/sbin/novelforge-ops" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest test' \
    >"$SANDBOX/root/home/novelops/.ssh/authorized_keys"
  printf '#!/bin/sh\nexit 0\n' >"$SANDBOX/root/usr/local/sbin/novelforge-ops"
  chmod +x "$SANDBOX/root/usr/local/sbin/novelforge-ops"
  : >"$SANDBOX/state/calls"

  cat >"$SANDBOX/bin/id" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "-u" ]; then echo 0; exit 0; fi
[ "${1:-}" = "novelops" ]
EOF
  cat >"$SANDBOX/bin/sshd" <<'EOF'
#!/bin/sh
if [ "${SSHD_INVALID:-0}" = 1 ]; then exit 1; fi
if [ "${1:-}" = "-t" ]; then exit 0; fi
port=22
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-p" ]; then shift; port=$1; fi
  shift
done
cat <<OUT
port $port
allowusers novelops
permitrootlogin no
passwordauthentication no
kbdinteractiveauthentication no
pubkeyauthentication yes
permittty no
allowtcpforwarding no
allowagentforwarding no
x11forwarding no
permittunnel no
gatewayports no
OUT
EOF
  cat >"$SANDBOX/bin/systemctl" <<'EOF'
#!/bin/sh
printf 'systemctl %s\n' "$*" >>"$NOVELFORGE_SSH_TEST_STATE/calls"
case "${1:-}" in
  is-active) [ -f "$NOVELFORGE_SSH_TEST_STATE/active" ] ;;
  is-enabled) [ -f "$NOVELFORGE_SSH_TEST_STATE/enabled" ] ;;
  enable) touch "$NOVELFORGE_SSH_TEST_STATE/enabled" ;;
  disable) rm -f "$NOVELFORGE_SSH_TEST_STATE/enabled" ;;
  restart)
    if [ -f "$NOVELFORGE_SSH_TEST_STATE/restart-fail-once" ]; then
      rm -f "$NOVELFORGE_SSH_TEST_STATE/restart-fail-once"
      exit 1
    fi
    touch "$NOVELFORGE_SSH_TEST_STATE/active"
    [ -f "$NOVELFORGE_SSH_TEST_STATE/no-listener" ] || touch "$NOVELFORGE_SSH_TEST_STATE/listener"
    ;;
  stop) rm -f "$NOVELFORGE_SSH_TEST_STATE/active" "$NOVELFORGE_SSH_TEST_STATE/listener" ;;
esac
exit 0
EOF
  cat >"$SANDBOX/bin/ss" <<'EOF'
#!/bin/sh
[ -f "$NOVELFORGE_SSH_TEST_STATE/listener" ] && printf 'LISTEN 0 128 0.0.0.0:22022 0.0.0.0:*\n'
exit 0
EOF
  cat >"$SANDBOX/bin/ufw" <<'EOF'
#!/bin/sh
printf 'ufw %s\n' "$*" >>"$NOVELFORGE_SSH_TEST_STATE/calls"
if [ "${1:-}" = status ]; then
  printf 'Status: active\n'
  [ -f "$NOVELFORGE_SSH_TEST_STATE/firewall-rule" ] && printf '22022/tcp ALLOW Anywhere\n'
  exit 0
fi
if [ "${1:-}" = allow ]; then
  [ -f "$NOVELFORGE_SSH_TEST_STATE/ufw-fail" ] && exit 1
  touch "$NOVELFORGE_SSH_TEST_STATE/firewall-rule"
  exit 0
fi
if [ "${1:-}" = --force ] && [ "${2:-}" = delete ]; then
  rm -f "$NOVELFORGE_SSH_TEST_STATE/firewall-rule"
fi
exit 0
EOF
  chmod +x "$SANDBOX/bin/"*
}

run_script() {
  env NOVELFORGE_SSH_TESTING=1 \
    NOVELFORGE_SSH_TEST_ROOT="$SANDBOX/root" \
    NOVELFORGE_SSH_TEST_STATE="$SANDBOX/state" \
    PATH="$SANDBOX/bin:$PATH" \
    bash "$SCRIPT" "$@"
}

check() {
  local name=$1
  shift
  if "$@"; then
    PASS=$((PASS + 1))
    printf 'ok - %s\n' "$name"
  else
    FAIL=$((FAIL + 1))
    printf 'not ok - %s\n' "$name"
  fi
}

scenario_success() {
  new_sandbox
  output=$(run_script 22022) || return 1
  grep -Fq '"ok":true' <<<"$output" \
    && grep -Fq '"primary_ssh_unchanged":true' <<<"$output" \
    && grep -Fq -- '-p 22022' "$SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service" \
    && grep -Fq -- '-o AllowUsers=novelops' "$SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service" \
    && [[ -f $SANDBOX/state/active && -f $SANDBOX/state/enabled && -f $SANDBOX/state/firewall-rule ]]
}

scenario_idempotent() {
  new_sandbox
  run_script 22022 >/dev/null || return 1
  run_script 22022 >/dev/null || return 1
  [[ $(grep -c '^ufw allow ' "$SANDBOX/state/calls") -eq 1 ]]
}

scenario_invalid_port() {
  new_sandbox
  ! run_script 22 >/dev/null 2>&1 \
    && [[ ! -e $SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service ]]
}

scenario_missing_restricted_key() {
  new_sandbox
  printf '%s\n' 'ssh-ed25519 AAAA unsafe' >"$SANDBOX/root/home/novelops/.ssh/authorized_keys"
  ! run_script 22022 >/dev/null 2>&1 \
    && [[ ! -e $SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service ]]
}

scenario_collision() {
  new_sandbox
  touch "$SANDBOX/state/listener"
  ! run_script 22022 >/dev/null 2>&1 \
    && [[ ! -e $SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service ]]
}

scenario_restart_failure_rolls_back() {
  new_sandbox
  unit="$SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service"
  printf 'old unit\n' >"$unit"
  touch "$SANDBOX/state/active" "$SANDBOX/state/enabled" "$SANDBOX/state/restart-fail-once"
  ! run_script 22022 >/dev/null 2>&1 \
    && [[ $(<"$unit") == 'old unit' ]] \
    && [[ -f $SANDBOX/state/active && -f $SANDBOX/state/enabled ]]
}

scenario_listener_failure_rolls_back() {
  new_sandbox
  touch "$SANDBOX/state/no-listener"
  ! run_script 22022 >/dev/null 2>&1 \
    && [[ ! -e $SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service ]] \
    && [[ ! -f $SANDBOX/state/enabled ]]
}

scenario_firewall_failure_rolls_back() {
  new_sandbox
  touch "$SANDBOX/state/ufw-fail"
  ! run_script 22022 >/dev/null 2>&1 \
    && [[ ! -e $SANDBOX/root/etc/systemd/system/novelforge-sshd-alt.service ]] \
    && [[ ! -f $SANDBOX/state/active ]]
}

check 'successful restricted listener install' scenario_success
check 'idempotent rerun' scenario_idempotent
check 'privileged port rejected' scenario_invalid_port
check 'unrestricted key rejected' scenario_missing_restricted_key
check 'foreign listener collision rejected' scenario_collision
check 'restart failure restores previous unit' scenario_restart_failure_rolls_back
check 'missing listener rolls back new unit' scenario_listener_failure_rolls_back
check 'firewall failure rolls back new unit' scenario_firewall_failure_rolls_back

printf 'SCENARIOS=8 PASSED=%d FAILED=%d\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
