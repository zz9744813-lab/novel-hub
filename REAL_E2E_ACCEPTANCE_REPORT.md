# REAL_E2E_ACCEPTANCE_REPORT

Generated: 2026-07-26T13:04:02.292622+00:00

## Environment
- URL: http://107.172.138.14/
- API ready: GET /health/ready → 200, detail db/provider/bindings ok
- PRIMARY_BASE_URL: http://new-api:3000/v1
- Frontend: index-CGms5xIT.js, index-DjIAeRFx.css
- Unit tests: 33 passed

## Auth matrix
| Case | Result |
|---|---|
| GET /api/books no token | 401 |
| GET /api/books bad token | 401 |
| GET /api/books good token | 200 |
| POST /api/books no token | 401 |

## T-01 single agent
- Role: query_planner
- Book: 3276b4a0-ab4f-4e30-9180-49f31a2d9e51
- AgentRun.status: completed
- completed_at: set
- model_name: deepseek-v4-flash
- route events: attempt 1 primary
- context packages: attempt 1

## Not run
T-02..T-09 full suite items (fallback/full chapter/patch/genre/research/kill/10ch/full security)

## Verdict
Partial real E2E OK. Full production gate NOT MET.
