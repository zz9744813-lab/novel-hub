"""Model Capability seed data.

Spec §10: provider /models discovers models at runtime; capability facts come
from this seed first (model_family knowledge), then provider metadata, then
probe. Context window is NEVER guessed from the model name alone.
"""

MODEL_CAPABILITY_SEED = {
    # ── OpenAI family ──
    "gpt-4o": {"context_window": 128000, "max_output_tokens": 16384, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "A"},
    "gpt-4o-mini": {"context_window": 128000, "max_output_tokens": 16384, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "gpt-4.1": {"context_window": 1048576, "max_output_tokens": 32768, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "A"},
    "gpt-4.1-mini": {"context_window": 1048576, "max_output_tokens": 32768, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "o3": {"context_window": 200000, "max_output_tokens": 100000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "S"},
    "o3-mini": {"context_window": 200000, "max_output_tokens": 100000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "A"},
    "o4-mini": {"context_window": 200000, "max_output_tokens": 100000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "A"},
    # ── Anthropic family ──
    "claude-3-5-sonnet-20241022": {"context_window": 200000, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "S"},
    "claude-3-5-haiku-20241022": {"context_window": 200000, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "A"},
    "claude-sonnet-4-20250514": {"context_window": 200000, "max_output_tokens": 64000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "S"},
    "claude-opus-4-20250514": {"context_window": 200000, "max_output_tokens": 32000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "S"},
    # ── DeepSeek ──
    "deepseek-chat": {"context_window": 64000, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "deepseek-reasoner": {"context_window": 64000, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "A"},
    # ── Qwen / 通义 ──
    "qwen-max": {"context_window": 32768, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "qwen-plus": {"context_window": 131072, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "qwen-turbo": {"context_window": 131072, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "C"},
    # ── GLM ──
    "glm-4-plus": {"context_window": 128000, "max_output_tokens": 4096, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "glm-4-flash": {"context_window": 128000, "max_output_tokens": 4096, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "C"},
    "glm-4.6": {"context_window": 200000, "max_output_tokens": 32768, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "A"},
    # ── Kimi / Moonshot ──
    "moonshot-v1-128k": {"context_window": 128000, "max_output_tokens": 8192, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": False, "quality_tier": "B"},
    "kimi-k2-0711-preview": {"context_window": 131072, "max_output_tokens": 32000, "supports_stream": True, "supports_json_schema": True, "supports_reasoning": True, "quality_tier": "S"},
    # ── 通义千问 / 智谱 via seed end; unknown models handled at runtime ──
}

# Default static prior quality for well-known families not in the seed.
FAMILY_QUALITY_TIER = {
    "gpt": "B", "o3": "A", "claude": "A", "gemini": "B",
    "deepseek": "B", "qwen": "B", "glm": "B", "kimi": "B",
}


def seed_for_model(model_id: str) -> dict | None:
    """Return seed capability dict for a known model id (exact, then family prefix)."""
    if model_id in MODEL_CAPABILITY_SEED:
        return MODEL_CAPABILITY_SEED[model_id]
    lowered = model_id.lower()
    for key, cap in MODEL_CAPABILITY_SEED.items():
        if lowered == key.lower() or lowered.startswith(key.lower() + "-"):
            return cap
    return None


def static_quality_score_for(model_id: str) -> float | None:
    """Map quality_tier → numeric prior score used in routing."""
    tier_scale = {"S": 95.0, "A": 90.0, "B": 80.0, "C": 70.0}
    cap = seed_for_model(model_id)
    if cap and cap.get("quality_tier") in tier_scale:
        return tier_scale[cap["quality_tier"]]
    return None
