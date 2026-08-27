"""v9.7 model classification: multi-evidence model kind inference (spec §13.1–§13.4).

Image/video/embedding/rerank/audio/moderation models are kept in the catalog
but NEVER enter text LLM probing, performance probes, role benchmarks, auto
route or session preflight.
"""
from __future__ import annotations

import logging

from app.models import ModelCatalog

logger = logging.getLogger("novelforge.model_autopilot.classification")

TEXT_KINDS = frozenset({"text_generation", "multimodal_text_generation"})

# Name heuristics: EXCLUSIONS ONLY — never promote unknown models by name.
NON_TEXT_HINTS = ("image", "imagen", "dall-e", "dalle", "flux", "stable-diffusion",
                  "sdxl", "janus", "video", "veo", "sora", "kling", "seedance",
                  "embedding", "embed", "rerank", "tts", "speech", "whisper",
                  "moderation", "wav", "audio", "transcribe")

KIND_BY_OUTPUT = {
    ("image",): "image_generation",
    ("video",): "video_generation",
    ("audio",): "audio_generation",
}
KIND_BY_INPUT_LABEL = {
    "embedding": "embedding",
    "rerank": "reranker",
    "speech": "speech_to_text",
    "transcribe": "speech_to_text",
    "moderation": "moderation",
}


def classify_catalog_model(catalog: ModelCatalog) -> None:
    """Best-effort deterministic classification; runs at sync time."""
    metadata = catalog.metadata_json or {}
    inp = list(metadata.get("input_modalities") or [])
    out = list(metadata.get("output_modalities") or [])
    name = (catalog.model_id or "").lower()

    kind = None
    source = "provider_metadata"

    if out:
        # 1. provider metadata: output modality is decisive when <text>
        if "text" not in out and len(out) == 1:
            kind = KIND_BY_OUTPUT.get(tuple(out), "unknown")
        elif "text" in out:
            kind = "multimodal_text_generation" if inp else "text_generation"

    # A prior successful configured text handshake is durable evidence when a
    # later /models sync still provides no modality metadata.  Decisive new
    # provider output metadata above is allowed to replace it.
    if (
        kind is None
        and catalog.classification_source == "configured_text_handshake"
        and catalog.text_generation_eligible
    ):
        return

    if kind is None:
        # 3/6: name heuristic — exclusions only
        if any(h in name for h in NON_TEXT_HINTS):
            kind = "unknown"
            source = "name_heuristic"

    if kind is None:
        kind = "unknown"
        source = "unknown"

    catalog.model_kind = kind
    catalog.input_modalities = inp
    catalog.output_modalities = out
    catalog.classification_source = source
    catalog.classification_confidence = 0.95 if source == "provider_metadata" else (0.6 if source == "name_heuristic" else None)

    if kind in TEXT_KINDS:
        catalog.text_generation_eligible = True
        catalog.evaluation_exclusion_reason = None
    else:
        catalog.text_generation_eligible = False
        catalog.evaluation_exclusion_reason = (
            f"non_text_model:{kind}" if kind != "unknown" else "kind_unknown_await_handshake"
        )


def eligible_text_candidates(catalogs: list[ModelCatalog]) -> list[ModelCatalog]:
    """Text-eligible only; everything else is excluded from LLM evaluation."""
    return [c for c in catalogs if c.text_generation_eligible]


def promote_configured_text_model(catalog: ModelCatalog) -> None:
    """Promote an explicitly configured model after a successful text ping.

    Many OpenAI-compatible ``/models`` responses expose only an id and owner,
    so metadata classification correctly leaves them unknown.  A successful
    L1 request with non-empty text is stronger runtime evidence than a name
    heuristic.  This promotion is intentionally available only to models that
    an operator already selected in ``AgentModelBinding``; arbitrary unknown
    catalog entries remain excluded.
    """

    inputs = list(catalog.input_modalities or [])
    outputs = list(catalog.output_modalities or [])
    if "text" not in outputs:
        outputs.append("text")
    catalog.model_kind = (
        "multimodal_text_generation" if any(item != "text" for item in inputs)
        else "text_generation"
    )
    catalog.output_modalities = outputs
    catalog.text_generation_eligible = True
    catalog.classification_source = "configured_text_handshake"
    catalog.classification_confidence = 1.0
    catalog.evaluation_exclusion_reason = None
    catalog.auto_route_enabled = True
