# AI__.md v3.0 实施记录

> 用户否决备份：不包含备份/恢复演练。

## 已落地

### PR-01 / PR-02
- reliability 表 0006；Run/Outbox；Typed Outcome；final-only 读导出；Patch CAS

### PR-03 Step Runner
- `step_runner.py` input_hash 复用 + Run lease CAS
- Pipeline：query_plan / chapter_plan / draft_scene / review

### PR-04 Final Artifact + 原子 Finalize（本轮）
- `final_artifact.py`：FinalArtifact / 场景拼接完整性 / finalization_key
- `state_extractor.py`：**只产候选**，不再写 StoryEvent/L1/L4
- `chapter_finalizer.py`：单事务
  - FOR UPDATE + xact advisory
  - finalization_key 幂等
  - immutable final ChapterVersion
  - Scene/Paragraph/Search + StoryEvent + L1 + L4 + finalized 指针
- Pipeline：`canon_extract` checkpoint → `commit_final_chapter_snapshot(..., validated_events=...)`
- **禁止** hash 不一致时压成单场景

## 验证
- `ARTIFACT_OK` / `EXTRACT_EMPTY_OK`
- Finalize + Canon：`se=1 l1=1 scenes=2 finals=1`
- 重放：`idempotent=True` 仍只有 1 个 final version
- `NO_SQUASH_OK` / `ALL_PR04_OK`
- `/health/ready` ready

## 未完
- PR-05 全量 strict Schema / PR-06 Context 硬预算
- retrieval/patch 全步骤 checkpoint
- §14 故障注入 + 10 章 Go/No-Go
- B-08 consistency 真实现 / B-12 pause 全边界深化

**不得宣称** 12 不变量全绿或无人值守 10 章。
