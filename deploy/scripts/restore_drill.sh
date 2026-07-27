#!/usr/bin/env bash
# BKP-003: restore drill for latest (or given) backup dir.
# Creates temporary Postgres container, restores dump, verifies table counts,
# optionally checks sqlite integrity. Does NOT touch production volumes.
set -euo pipefail

ROOT="/root/novelforge"
BACKUP_ROOT="${ROOT}/data/backups"
BACKUP_DIR="${1:-}"
if [[ -z "${BACKUP_DIR}" ]]; then
  BACKUP_DIR="$(ls -1dt "${BACKUP_ROOT}"/20* 2>/dev/null | head -1 || true)"
fi
if [[ -z "${BACKUP_DIR}" || ! -d "${BACKUP_DIR}" ]]; then
  echo "No backup dir found under ${BACKUP_ROOT}" >&2
  exit 1
fi

DUMP="${BACKUP_DIR}/novelforge.dump"
SQLITE="${BACKUP_DIR}/new-api.sqlite"
REPORT="${BACKUP_DIR}/RESTORE_DRILL.txt"
TMP_NAME="novelforge-restore-drill-$$"
NET="novelforge_restore_drill_$$"
PASS="RestoreDrillOnly"
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cleanup() {
  docker rm -f "${TMP_NAME}" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

{
  echo "restore_drill_start=${START_TS}"
  echo "backup_dir=${BACKUP_DIR}"
  echo "dump=${DUMP}"
} > "${REPORT}"

if [[ ! -f "${DUMP}" ]]; then
  echo "FAIL: missing dump" | tee -a "${REPORT}"
  exit 1
fi

# Verify sha if present
if [[ -f "${DUMP}.sha256" ]]; then
  if (cd "${BACKUP_DIR}" && sha256sum -c novelforge.dump.sha256); then
    echo "sha256_pg=ok" >> "${REPORT}"
  else
    echo "sha256_pg=FAIL" | tee -a "${REPORT}"
    exit 1
  fi
fi

docker network create "${NET}" >/dev/null
docker run -d --name "${TMP_NAME}" --network "${NET}" \
  -e POSTGRES_PASSWORD="${PASS}" \
  -e POSTGRES_USER=restore \
  -e POSTGRES_DB=restore \
  postgres:16-alpine >/dev/null

# Wait ready
for i in $(seq 1 30); do
  if docker exec "${TMP_NAME}" pg_isready -U restore -d restore >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Restore
docker exec -i "${TMP_NAME}" pg_restore -U restore -d restore --no-owner --role=restore < "${DUMP}" \
  2>>"${REPORT}.pg_restore.log" || true

# pg_restore may warn on extensions; verify core tables
TABLES="$(docker exec "${TMP_NAME}" psql -U restore -d restore -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
BOOKS="$(docker exec "${TMP_NAME}" psql -U restore -d restore -Atc "SELECT count(*) FROM books;" 2>/dev/null || echo 0)"
CHAPS="$(docker exec "${TMP_NAME}" psql -U restore -d restore -Atc "SELECT count(*) FROM chapters WHERE status='finalized';" 2>/dev/null || echo 0)"
VERS="$(docker exec "${TMP_NAME}" psql -U restore -d restore -Atc "SELECT count(*) FROM chapter_versions;" 2>/dev/null || echo 0)"

{
  echo "public_tables=${TABLES}"
  echo "books=${BOOKS}"
  echo "finalized_chapters=${CHAPS}"
  echo "chapter_versions=${VERS}"
} >> "${REPORT}"

SQLITE_OK="skip"
if [[ -f "${SQLITE}" ]]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    if sqlite3 "${SQLITE}" "PRAGMA integrity_check;" | grep -q '^ok$'; then
      SQLITE_OK=ok
    else
      SQLITE_OK=fail
    fi
  else
    # use temp container with sqlite
    if docker run --rm -v "${BACKUP_DIR}:/b:ro" nouchka/sqlite3 /b/new-api.sqlite "PRAGMA integrity_check;" 2>/dev/null | grep -q '^ok$'; then
      SQLITE_OK=ok
    else
      # fallback: file non-empty
      if [[ -s "${SQLITE}" ]]; then SQLITE_OK=file_present; else SQLITE_OK=fail; fi
    fi
  fi
fi
echo "sqlite_integrity=${SQLITE_OK}" >> "${REPORT}"

END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "restore_drill_end=${END_TS}" >> "${REPORT}"

# Pass criteria
if [[ "${TABLES}" -ge 20 && "${BOOKS}" -ge 1 ]]; then
  echo "RESULT=PASS" | tee -a "${REPORT}"
  cat "${REPORT}"
  exit 0
else
  echo "RESULT=FAIL tables=${TABLES} books=${BOOKS}" | tee -a "${REPORT}"
  cat "${REPORT}"
  exit 2
fi
