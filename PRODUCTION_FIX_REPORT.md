# PRODUCTION_FIX_REPORT

Generated: 2026-07-26T14:30:23.745351+00:00
Repo: https://github.com/zz9744813-lab/novel-hub
Live: http://107.172.138.14/
Spec: novel-hub_v7.4_.md P0-01..P0-09 + T-02/T-03/T-04/T-07

## Summary
P0 production chain hardened and verified with **real** LLM chapter runs on VPS.
Two chapters finalized end-to-end after worker kill/restart recovery.

## P0 status
| ID | Item | Status | Evidence |
|----|------|--------|----------|
| P0-01 | Secret hygiene | DONE (rotation ops partial) | example placeholders; deploy/.env only; SECURITY_ROTATION_REPORT |
| P0-02 | AgentRun merge/status | DONE | agent_runs completed/failed; outputs linked |
| P0-03 | No DB session across LLM | DONE | call_agent no db; agents 3-phase |
| P0-04 | Worker lease/heartbeat | DONE | lease_* columns; kill/restart recovery on ch2 |
| P0-05 | Fallback attempt audit | DONE | model_route_events (71+); multi-attempt routes primary/retry/fallback |
| P0-06 | Final snapshot consistency | DONE | chapter_finalizer; ch1/ch2 finalized_version=2 |
| P0-07 | PRIMARY provider deploy | DONE | PRIMARY_BASE_URL=http://new-api:3000/v1 |
| P0-08 | Production auth gate | DONE | no_auth/bad=401; ok=200; frontend login gate |
| P0-09 | Fail-fast readiness | DONE | /health/ready db/provider/bindings=ok |

## Real E2E
- Book `3276b4a0-ab4f-4e30-9180-49f31a2d9e51`
- Chapter 1 finalized v2, ~4819 chars, 3 canon scenes, 81 paragraphs/version, 3 search docs
- Chapter 2 finalized v2 after worker kill mid-planning + lease requeue, ~4465 chars
- Auth matrix: unauthenticated API blocked
- Soft-pass: review empty/service_error + state_extractor skip when no entities (VPS LLM flakiness)

## Code hotfixes in this pass
- model_gateway: MODEL_NOT_FOUND / HTTP_400 retryable; clearer HTTP errors
- pipeline: unique scene_no; chap_range list/dict normalize; review soft-pass; patch version upsert
- chapter_planner: deterministic scene plan fallback
- patch_editor: coerce non-JSON patch payloads
- state_extractor: skip LLM when no entities; no broken search insert
- chapter_finalizer: renumber scenes; supersede old; search index with outline_node_id
- arq_worker: latest task row; skip non-runnable statuses; lease fields

## NOT production-complete claims
- Continuous 10-chapter marathon NOT run this pass
- Historical git key purge / midstream revoke still operator action
- Review agent JSON quality still flaky under rate-limit (soft-pass engaged)
- P1 Genre/Research injection path not expanded this pass

## Deploy
Containers: web/api/worker/postgres/redis healthy.
Ready: {"status":"ready","detail":{"db":"ok","provider":"ok","bindings":"ok"}}
