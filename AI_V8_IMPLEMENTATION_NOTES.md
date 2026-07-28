# NovelForge v8.0 implementation notes (partial — not full DoD)

Branch: `refactor/bookshelf-import-prompt-studio`
Spec: NovelForge-v8-.md

## Landed this batch (Phase 0–1 skeleton + partial 4/5/6)

### Database
- Alembic `0007_v8_library_import_prompt`
- books: subtitle, logline, tags, cover_*, planned_chapters, lifecycle_status, …
- book_profiles, book_sources, import_sessions + events/artifacts/conflicts
- location_cards, character_relationships, outline_volumes, writing_constraints
- prompt_template_versions, prompt_test_runs
- outline_nodes: volume_id, arc_id, source_refs, import_artifact_id

### API (independent routers — not stacked into routes.py)
- `GET /api/library/books` — bookshelf aggregation (no N+1)
- `GET /api/library/books/{id}` — book home summary
- `POST /api/import-sessions` — upload → extract blocks → sanitize candidates → preview_ready **without creating Book**
- preview / resolve conflict / commit (minimal Book+BookProfile) / cancel
- Prompt Studio: agents, templates CRUD-ish, compatibility, test (structural), activate, compiled-preview

### Frontend
- Global nav: 我的书架 / 写作任务 / 参考资料库 / 系统设置
- Book nav: 作品首页 / 大纲 / 章节 / 写作台 / 记忆 / 提示词工坊 / … / 高级诊断
- LibraryPage + BookCard (stable CSS cover)
- ImportWizard: 企划书 vs 空白；确认后才 commit
- BookHomePage: 继续写下一章
- PromptStudioPage skeleton

### ContextAssembler
- Wires book_profile, volume, character_cards, relationships, locations, writing_constraints, genre, approved external research when rows exist
- budget remains **record_only**

## Explicitly NOT done (spec DoD)
- Multi-agent LLM extractors (classifier/sanitizer/entity/outline v2…)
- Full atomic multi-entity commit (Phase 3 full)
- Worker checkpointed import pipeline
- WebSocket real events
- Playwright E2E / 西荒慈父 Golden full extract
- Cover upload pipeline 320×480
- Feature-flag dual UI (currently v8 UI is default on this branch)

## Ops
- Upload dir: `/app/data/imports` (compose mount `../data/imports`)
- Feature flags in settings: FEATURE_LIBRARY_V2 / IMPORT_V2 / PROMPT_STUDIO (default true)


## Phase 2 (landed, partial DoD)

- Worker: `run_import_pipeline_job` multi-step LLM extract with artifact checkpoints
- Upload returns `analyzing` then poll until `preview_ready` / `needs_human`
- Deterministic outline regex fallback merged with LLM
- Commit creates characters/relationships/outline nodes/volumes/plot threads/writing constraints
- Smoke (2026-07-28): sample md → 3 chars, 4 outline nodes, 3 plot threads, 3 writing rules; book `83fbd711-…`
- Known gaps: world_rules/locations often empty under LLM variance; 429 rate-limit slows pipeline; no Playwright E2E; GitHub push still auth-blocked


## World/Location harden + 西荒 Golden smoke

- deterministic_world_from_text + merge_world (rules/locations regex fallback)
- commit writes location_cards + world_rules
- Smoke 西荒·弃渊夜 (session 59342df5… → book 17ffd9a8…):
  chars=3, nodes=4, rules=1, locs=4 (弃渊城/夜市/慈父宅/猎渊崖), wr=8, pt=6, vols=2
- Branch only: refactor/bookshelf-import-prompt-studio (NOT main)
- LLM 429/503 still slows pipeline (~10min under load)
