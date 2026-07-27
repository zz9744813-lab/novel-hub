#!/usr/bin/env bash
# NovelForge + New-API backup (P0 BKP-001/002)
# - PostgreSQL: pg_dump -Fc (consistent snapshot)
# - New API SQLite: VACUUM INTO or Online Backup when possible
# - Files: books / exports / references (tar.gz)
# Does NOT print secrets. Dest: /root/novelforge/data/backups
set -euo pipefail

UTC_TS="$(date -u +%Y%m%dT%H%M%SZ)"
ROOT="/root/novelforge"
BACKUP_ROOT="${ROOT}/data/backups"
DAY_DIR="${BACKUP_ROOT}/${UTC_TS}"
mkdir -p "${DAY_DIR}"

log() { echo "[backup ${UTC_TS}] $*"; }

# --- PostgreSQL (via container) ---
PG_CONTAINER="${PG_CONTAINER:-novelforge-postgres-1}"
if docker ps --format '{{.Names}}' | grep -qx "${PG_CONTAINER}"; then
  OUT_PG="${DAY_DIR}/novelforge.dump"
  # Read DB name/user from container env without dumping password to stdout logs
  docker exec "${PG_CONTAINER}" sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "${OUT_PG}"
  sha256sum "${OUT_PG}" > "${OUT_PG}.sha256"
  # Verify listability
  if docker exec -i "${PG_CONTAINER}" pg_restore --list > /dev/null 2>&1 < "${OUT_PG}"; then
    log "postgres dump OK $(wc -c < "${OUT_PG}") bytes"
  else
    log "WARN: pg_restore --list failed for ${OUT_PG}"
  fi
else
  log "SKIP postgres: container ${PG_CONTAINER} not running"
fi

# --- New API SQLite ---
NEW_API_DB="${NEW_API_DB:-/root/new-api/data/one-api.db}"
if [[ -f "${NEW_API_DB}" ]]; then
  OUT_SQLITE="${DAY_DIR}/new-api.sqlite"
  # Prefer VACUUM INTO for consistency when sqlite3 available
  if command -v sqlite3 >/dev/null 2>&1; then
    rm -f "${OUT_SQLITE}"
    if sqlite3 "${NEW_API_DB}" "VACUUM INTO '${OUT_SQLITE}'" 2>/dev/null; then
      :
    else
      # Fallback: online backup API
      sqlite3 "${NEW_API_DB}" ".backup '${OUT_SQLITE}'" 2>/dev/null || cp -a "${NEW_API_DB}" "${OUT_SQLITE}"
    fi
    if sqlite3 "${OUT_SQLITE}" "PRAGMA integrity_check;" 2>/dev/null | grep -q '^ok$'; then
      log "sqlite backup OK $(wc -c < "${OUT_SQLITE}") bytes integrity=ok"
    else
      log "WARN: sqlite integrity_check not ok"
    fi
  else
    cp -a "${NEW_API_DB}" "${OUT_SQLITE}"
    log "sqlite copied (no sqlite3 CLI) $(wc -c < "${OUT_SQLITE}") bytes"
  fi
  sha256sum "${OUT_SQLITE}" > "${OUT_SQLITE}.sha256"
else
  log "SKIP sqlite: ${NEW_API_DB} missing"
fi

# --- File volumes ---
OUT_FILES="${DAY_DIR}/files.tgz"
tar -czf "${OUT_FILES}" \
  -C "${ROOT}/data" books exports references 2>/dev/null \
  || tar -czf "${OUT_FILES}" -C "${ROOT}/data" . 2>/dev/null || true
if [[ -f "${OUT_FILES}" ]]; then
  sha256sum "${OUT_FILES}" > "${OUT_FILES}.sha256"
  log "files archive OK $(wc -c < "${OUT_FILES}") bytes"
fi

# --- Manifest ---
{
  echo "ts=${UTC_TS}"
  echo "host=$(hostname)"
  echo "git=$(cd "${ROOT}" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  ls -la "${DAY_DIR}"
} > "${DAY_DIR}/MANIFEST.txt"

# Retention: keep last 24 hourly dirs
cd "${BACKUP_ROOT}"
ls -1dt 20* 2>/dev/null | tail -n +25 | xargs -r rm -rf

log "done -> ${DAY_DIR}"
echo "${DAY_DIR}"
