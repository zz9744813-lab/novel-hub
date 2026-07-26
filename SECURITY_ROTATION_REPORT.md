# SECURITY_ROTATION_REPORT

Date (UTC): 2026-07-26T12:56:28.799630+00:00
Baseline: main@ab3861105864ab5f9698677934edb8f98714a6f7

## P0-01 Actions

1. `deploy/.env.example` rewritten to placeholders only (`PRIMARY_API_KEY=replace-with-real-key`, etc.).
2. `.gitignore` enforces: `.env`, `*.env`, `!.env.example`, `deploy/.env`, `*.bak`, `*.bak2`.
3. `backend/alembic.ini` sqlalchemy.url password replaced with placeholder (must not ship real secrets).
4. Historical scan: previous commits contained a New-API style key in `deploy/.env.example` (git history still has the old blob until history rewrite).
5. **Operator action required (cannot be automated safely from this agent session without New-API admin credentials):**
   - Revoke the old leaked key in New-API / midstream console.
   - Mint a new key and write it ONLY to VPS `deploy/.env` (never git).
   - Confirm `/v1/models` returns 200 with the new key from the `novelforge_internal` network (`PRIMARY_BASE_URL=http://new-api:3000/v1`).

## Scan notes

- Current working tree `deploy/.env.example` has no live key.
- Live secrets remain only on VPS `deploy/.env` (gitignored).
- Full history purge (BFG/filter-repo) deferred until key is rotated server-side; until then treat old key as compromised.

## CI

- Added `.github/workflows/ci.yml` with Gitleaks + pytest + frontend build hooks (see commit).

## Status

- Code-side secret hygiene: **DONE**
- Server-side key revocation: **OPERATOR REQUIRED** (NOT VERIFIED by agent)


## E2E update (2026-07-26T14:30:23.745351+00:00)
- Production auth gate verified (401/401/200)
- No secrets committed in this push (deploy/.env untracked)
- Operator still must revoke any historically leaked midstream keys
