"""Deterministic style metrics engine (spec §22, §30-§34).

Python measures what it can deterministically — sentence/paragraph length,
punctuation frequency, dialogue ratio, lexical diversity, emotion-expression
density — so the LLM (style_analyzer) only judges the high-level semantic
dimensions it cannot count. jieba is optional; without it we fall back to
character bigrams for lexical diversity.
"""
from __future__ import annotations

import re
from collections import Counter
from statistics import mean, median, pstdev

try:
    import jieba  # type: ignore

    _JIEBA = True
except Exception:  # pragma: no cover - optional dependency
    jieba = None
    _JIEBA = False


# ── segmentation ────────────────────────────────────────────
_SENTENCE_END = "。！？!?…；;"
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?…；;])\s*|\n+")

# Dialogue delimiters (Chinese + ASCII quotes)
_DIALOGUE_RE = re.compile(r"[「」“”\"'][^「」“”\"'\n]{1,120}[「」“”\"']")

# Explicit emotion words (small seed lexicon; expandable)
_EMOTION_WORDS = (
    "愤怒 悲伤 喜悦 恐惧 惊讶 痛苦 快乐 忧愁 焦虑 兴奋 愧疚 羞耻 嫉妒 憎恨 "
    "温柔 冷漠 慌乱 激动 委屈 绝望 欣喜 不安 烦躁 感动 甜蜜 苦涩 狂喜 惊惧 "
    "悲恸 忐忑 心酸 欣慰 痛快 厌倦 迷茫 陶醉 怜惜 震怒 惊恐 狂喜 哀伤"
).split()

# Somatic / body-signal words (behavior-based emotion, spec §34)
_BODY_SIGNALS = (
    "颤抖 发抖 脸红 苍白 发白 出汗 冷汗 心跳 心悸 屏住 呼吸 哽咽 喉头 "
    "眼眶 泪水 眼泪 拳头 攥紧 后退 僵住 愣住 哆嗦 战栗 起鸡皮疙瘩 窒息"
).split()

# Internal-monologue markers
_INTERNAL_MARKERS = (
    "心想 暗想 暗忖 思忖 寻思 心道 默念 暗道 自忖 转念 心说 腹诽 嘀咕"
).split()

# Function words (Chinese) for the function-word vector
_FUNCTION_WORDS = (
    "的 了 是 在 我 你 他 她 它 们 这 那 就 都 而 也 又 还 把 被 让 给 "
    "与 和 或 但 却 才 刚 已经 正在 不 没 很 太 更 最 只 却 因为 所以 "
    "如果 虽然 但是 然而 于是 然后 接着 便 即 亦 之 其 者 所 于 从 到 向"
).split()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def _tokenize(text: str) -> list[str]:
    if _JIEBA and jieba is not None:
        return [t for t in jieba.lcut(text) if t.strip()]
    # fallback: character bigrams (rough but dependency-free)
    chars = re.sub(r"\s+", "", text)
    if len(chars) < 2:
        return list(chars)
    return [chars[i : i + 2] for i in range(len(chars) - 1)]


def _count_words(text: str, lexicon: tuple[str, ...]) -> int:
    hit = 0
    for w in lexicon:
        hit += text.count(w)
    return hit


# ── main entry ──────────────────────────────────────────────
def extract_style_metrics(text: str) -> dict:
    """Extract deterministic style metrics from a Chinese prose sample."""
    text = text or ""
    char_count = len(re.sub(r"\s+", "", text))

    sentences = _sentences(text)
    paragraphs = _paragraphs(text)

    sen_lens = [len(re.sub(r"\s+", "", s)) for s in sentences]
    par_lens = [len(re.sub(r"\s+", "", p)) for p in paragraphs]

    # punctuation per 1000 chars
    def per_1000(ch: str) -> float:
        n = text.count(ch)
        return round(n / max(char_count, 1) * 1000, 2)

    commas = per_1000("，") + per_1000(",")
    periods = per_1000("。")
    questions = per_1000("？") + per_1000("?")
    exclamations = per_1000("！") + per_1000("!")

    ellipsis = text.count("…") + text.count("...")
    dash = text.count("——") + text.count("—") + text.count("-")
    semicolon = text.count("；") + text.count(";")
    total_punct = commas + periods + questions + exclamations + ellipsis + dash + semicolon

    # lexical diversity
    tokens = _tokenize(text)
    token_count = len(tokens)
    vocab = Counter(tokens)
    type_token_ratio = round(len(vocab) / max(token_count, 1), 4)

    # dialogue
    dialogue_spans = _DIALOGUE_RE.findall(text)
    dialogue_chars = sum(len(re.sub(r"[「」“”\"']", "", s)) for s in dialogue_spans)
    dialogue_ratio = round(dialogue_chars / max(char_count, 1), 4)
    dialogue_turn_lens = [len(re.sub(r"[「」“”\"']", "", s)) for s in dialogue_spans]
    speaker_switch = len(dialogue_spans)

    # emotion expression (spec §34)
    emotion_hits = _count_words(text, _EMOTION_WORDS)
    body_hits = _count_words(text, _BODY_SIGNALS)
    internal_hits = _count_words(text, _INTERNAL_MARKERS)

    surface = {
        "sentence_chars_mean": round(mean(sen_lens), 2) if sen_lens else 0.0,
        "sentence_chars_p50": round(median(sen_lens), 2) if sen_lens else 0.0,
        "sentence_chars_p90": round(_percentile([float(x) for x in sen_lens], 0.9), 2),
        "sentence_chars_std": round(pstdev(sen_lens), 2) if len(sen_lens) > 1 else 0.0,
        "paragraph_chars_mean": round(mean(par_lens), 2) if par_lens else 0.0,
        "paragraph_chars_p50": round(median(par_lens), 2) if par_lens else 0.0,
        "paragraph_chars_p90": round(_percentile([float(x) for x in par_lens], 0.9), 2),
        "paragraph_chars_std": round(pstdev(par_lens), 2) if len(par_lens) > 1 else 0.0,
        "commas_per_1000": commas,
        "periods_per_1000": periods,
        "questions_per_1000": questions,
        "exclamations_per_1000": exclamations,
        "ellipsis_ratio": round(ellipsis / max(total_punct, 1), 4),
        "dash_ratio": round(dash / max(total_punct, 1), 4),
        "semicolon_ratio": round(semicolon / max(total_punct, 1), 4),
        "lexical_diversity": type_token_ratio,
        "token_count": token_count,
    }

    rhythm = {
        "sentence_length_cv": round(pstdev(sen_lens) / max(mean(sen_lens), 1), 4) if len(sen_lens) > 1 else 0.0,
        "short_sentence_ratio": round(sum(1 for x in sen_lens if x <= 10) / max(len(sen_lens), 1), 4),
        "long_sentence_ratio": round(sum(1 for x in sen_lens if x >= 40) / max(len(sen_lens), 1), 4),
        "paragraph_length_cv": round(pstdev(par_lens) / max(mean(par_lens), 1), 4) if len(par_lens) > 1 else 0.0,
    }

    dialogue = {
        "dialogue_ratio": dialogue_ratio,
        "dialogue_turn_length_p50": round(median(dialogue_turn_lens), 2) if dialogue_turn_lens else 0.0,
        "dialogue_turn_length_p90": round(_percentile([float(x) for x in dialogue_turn_lens], 0.9), 2),
        "speaker_switch_rate": round(speaker_switch / max(len(sentences), 1), 4),
    }

    emotion = {
        "explicit_emotion_word_ratio": round(emotion_hits / max(char_count, 1) * 1000, 3),
        "body_signal_ratio": round(body_hits / max(char_count, 1) * 1000, 3),
        "internal_monologue_ratio": round(internal_hits / max(char_count, 1) * 1000, 3),
    }

    return {
        "surface": surface,
        "rhythm": rhythm,
        "dialogue": dialogue,
        "emotion": emotion,
        "meta": {
            "char_count": char_count,
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
            "jieba_available": _JIEBA,
        },
    }


def compute_fingerprint(metric_vector: dict) -> list[float]:
    """Flatten the deterministic metric vector into a fingerprint (spec §24).

    Used for Burrows'-Delta-style distance / drift, not author attribution.
    """
    keys = [
        "surface.sentence_chars_mean",
        "surface.sentence_chars_std",
        "surface.paragraph_chars_mean",
        "surface.commas_per_1000",
        "surface.periods_per_1000",
        "surface.exclamations_per_1000",
        "surface.ellipsis_ratio",
        "surface.lexical_diversity",
        "rhythm.sentence_length_cv",
        "rhythm.short_sentence_ratio",
        "rhythm.long_sentence_ratio",
        "dialogue.dialogue_ratio",
        "dialogue.speaker_switch_rate",
        "emotion.explicit_emotion_word_ratio",
        "emotion.body_signal_ratio",
        "emotion.internal_monologue_ratio",
    ]
    vec: list[float] = []
    for key in keys:
        section, field = key.split(".")
        vec.append(float(metric_vector.get(section, {}).get(field, 0.0) or 0.0))
    return vec


def fingerprint_distance(a: list[float], b: list[float]) -> float:
    """Euclidean distance between two metric fingerprints."""
    if not a or not b or len(a) != len(b):
        return float("inf")
    s = sum((x - y) ** 2 for x, y in zip(a, b))
    return round(s ** 0.5, 4)
