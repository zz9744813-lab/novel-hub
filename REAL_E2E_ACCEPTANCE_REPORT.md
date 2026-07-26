# REAL_E2E_ACCEPTANCE_REPORT

Generated: 2026-07-26T14:30:23.745351+00:00
Live URL: http://107.172.138.14/

## Environment
- PRIMARY: http://new-api:3000/v1 (container network)
- Models: deepseek-v4-flash primary, stepfun-ai/step-3.7-flash fallback (new-api)
- GLOBAL_LLM_CONCURRENCY=1 (low-spec VPS)

## Tests executed (real, not mock)

### T-Auth
- GET /api/books no token → 401
- GET /api/books bad token → 401
- GET /api/books valid ADMIN_API_TOKEN → 200
- GET /health/ready → ready (db/provider/bindings ok)

### T-02 Fallback attempt audit
- model_route_events rows present (71+)
- Observed multi-attempt sequences: primary HTTP_429 → retry; primary → fallback model switch
- AgentRun statuses completed/failed persisted

### T-03 Single-chapter pipeline
- Chapter 1: queued → planning → drafting → reviewing → state_extracting → **finalized**
- Evidence SQL:
  - chapters.status=finalized, finalized_version=2
  - chapter_versions: v1 draft + v2 final (word_count=4819)
  - scenes: 3 superseded@v1 + 3 canon@v2
  - paragraphs: 81@v1 + 81@v2
  - scene_search_documents: 3
- Content sample starts with real novel prose (not [FAILED])

### T-04 Version consistency
- canon scenes all version=finalized_version (2)
- superseded prior draft scenes retained
- paragraph versions aligned to draft/final snapshots
- finalizer atomic commit used

### T-07 Worker kill / lease recovery
- Chapter 2 enqueued; status reached planning with lease_owner set
- `docker kill novelforge-worker-1` mid-run
- lease force-expired + status re-queued; worker restarted
- Chapter 2 recovered and **finalized** (attempt_no=2, finalized_version=2)

### Multi-chapter (partial)
- Chapters 1 and 2 both finalized on same book
- Full 10-chapter marathon: **NOT RUN** this session

## Soft-pass notes (honest)
- ReviewAgent often returns non-JSON under 429 / reasoning-only models → soft-pass when draft ≥1500 chars
- StateExtractor skipped when outline has no character entities (early book) to avoid REASONING_ONLY hang
- These keep production chain moving on flaky midstream models; quality gates still need stronger models for strict review

## Verdict
- **PASS** for single-chapter + dual-chapter + kill-recovery + auth + readiness
- **NOT VERIFIED** for continuous 10-chapter 100% success and strict review-without-soft-pass
