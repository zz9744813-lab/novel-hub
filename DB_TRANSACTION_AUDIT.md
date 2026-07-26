# DB_TRANSACTION_AUDIT

Generated: 2026-07-26T14:30:23.745351+00:00

## Design
- `call_agent`: short Session for read binding + create AgentRun → close → LLM stream → short Session merge AgentRun + write Output/Usage/route events
- Pipeline phases open `async with async_session_factory()` only around DB IO, never around LLM awaits
- Finalization: `commit_final_chapter_snapshot` single transaction for version/scenes/paragraphs/search/final pointer

## Observed production evidence
- Chapter 1 final: versions 1(draft)+2(final); scenes superseded then canon; paragraphs dual-version
- Chapter 2 same pattern after worker restart
- No long-held Session across multi-minute draft_writer calls (architecture + code path)
- Worker advisory lock prevents concurrent double pipeline on same chapter

## Lease columns (chapter_tasks)
- lease_owner / lease_expires_at / heartbeat_at present and used
- Kill mid-run left lease_owner set; requeue after expire allowed recovery

## Integrity fixes applied
- Unique scene_no per chapter/version (planner often returned all scene_no=1)
- SceneSearchDocument requires outline_node_id → indexing only in finalizer
- ChapterVersion upsert on re-run / patch version collision

## Residual risk
- Soft-pass finalizes without perfect review JSON
- Full pg_stat_activity long-tx capture during LLM not retained as file artifact this run
