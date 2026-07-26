# DB_TRANSACTION_AUDIT

Generated: 2026-07-26T13:04:02.292622+00:00

## Design
- call_agent short sessions only for create run / binding / route+context / output+status
- stream_with_retry outside sessions
- pipeline phases: short SQL sessions then session-free agents

## Evidence
- call_agent has no db param
- real call completed with Run/Output/Route persisted
- soak not executed

## Residual
- extract_and_commit write session must stay short after LLM
