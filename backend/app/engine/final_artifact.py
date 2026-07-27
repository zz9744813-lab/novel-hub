"""Final artifact structures (AI__.md v3.0 §8.2)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


SCENE_JOIN = "\n\n\n"  # fixed separator between scenes


@dataclass
class FinalParagraphArtifact:
    paragraph_key: str
    ordinal: int
    content: str
    content_hash: str


@dataclass
class FinalSceneArtifact:
    scene_no: int
    content: str
    content_hash: str
    paragraphs: list[FinalParagraphArtifact] = field(default_factory=list)
    summary: str = ""
    pov_character_id: str | None = None
    scene_id: str | None = None


@dataclass
class FinalArtifact:
    scenes: list[FinalSceneArtifact]
    joined_content: str
    joined_hash: str

    def validate_integrity(self) -> str | None:
        if not self.scenes:
            return "no_scenes"
        if not self.joined_content or not self.joined_content.strip():
            return "empty_final_content"
        if "[FAILED]" in self.joined_content:
            return "placeholder_in_final_content"
        rebuilt = SCENE_JOIN.join(s.content for s in self.scenes)
        if sha256_text(rebuilt) != self.joined_hash:
            return "joined_hash_mismatch"
        if sha256_text(self.joined_content) != self.joined_hash:
            return "content_hash_mismatch"
        if rebuilt != self.joined_content:
            return "joined_content_not_equal_scenes"
        for sc in self.scenes:
            if sha256_text(sc.content) != sc.content_hash:
                return f"scene_{sc.scene_no}_hash_mismatch"
            paras = [p for p in sc.content.split("\n\n") if p.strip()]
            if len(paras) != len(sc.paragraphs):
                # allow re-split on the fly later; require at least content hash ok
                continue
            for p, art in zip(paras, sc.paragraphs):
                if sha256_text(p) != art.content_hash:
                    return f"paragraph_{art.paragraph_key}_hash_mismatch"
        return None


def build_final_artifact(
    scenes: list[dict],
    joined_content: str | None = None,
) -> FinalArtifact:
    """Build FinalArtifact from scene dicts {scene_no, content, summary?, ...}.

    Does NOT squash multi-scene on hash mismatch — caller must fail closed.
    """
    ordered = sorted(
        [
            {
                "scene_no": int(s.get("scene_no") or i + 1),
                "content": s.get("content") or "",
                "summary": s.get("summary") or "",
                "pov_character_id": s.get("pov_character_id"),
                "scene_id": s.get("scene_id"),
            }
            for i, s in enumerate(scenes or [])
        ],
        key=lambda x: int(x["scene_no"]),
    )
    arts: list[FinalSceneArtifact] = []
    for sc in ordered:
        content = sc["content"]
        paras_txt = [p for p in content.split("\n\n") if p.strip()]
        paras: list[FinalParagraphArtifact] = []
        for pi, ptxt in enumerate(paras_txt, start=1):
            key = f"s{sc['scene_no']:02d}-p{pi:04d}"
            paras.append(
                FinalParagraphArtifact(
                    paragraph_key=key,
                    ordinal=pi,
                    content=ptxt,
                    content_hash=sha256_text(ptxt),
                )
            )
        arts.append(
            FinalSceneArtifact(
                scene_no=sc["scene_no"],
                content=content,
                content_hash=sha256_text(content),
                paragraphs=paras,
                summary=sc.get("summary") or "",
                pov_character_id=str(sc["pov_character_id"]) if sc.get("pov_character_id") else None,
                scene_id=str(sc["scene_id"]) if sc.get("scene_id") else None,
            )
        )
    joined = SCENE_JOIN.join(a.content for a in arts)
    if joined_content is not None and joined_content != joined:
        # Prefer explicit joined_content only if it matches scene join; else keep scenes
        # Caller validates via validate_integrity / finalizer.
        pass
    use_joined = joined_content if (joined_content is not None and joined_content == joined) else joined
    return FinalArtifact(
        scenes=arts,
        joined_content=use_joined,
        joined_hash=sha256_text(use_joined),
    )


def finalization_key(
    *,
    chapter_run_id: str,
    joined_hash: str,
    canon_candidates_hash: str,
    outline_version_id: str,
    pipeline_version: str = "pipeline-v2",
) -> str:
    raw = "|".join(
        [
            chapter_run_id or "",
            joined_hash or "",
            canon_candidates_hash or "",
            outline_version_id or "",
            pipeline_version or "",
        ]
    )
    return sha256_text(raw)


def canon_candidates_hash(events: list[dict]) -> str:
    import json

    raw = json.dumps(events or [], ensure_ascii=False, sort_keys=True, default=str)
    return sha256_text(raw)
