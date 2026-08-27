"""Immutable, synthetic v9.8 model-evaluation suite definitions.

The cases intentionally contain no imported fiction text and no author-specific
style target.  They measure reusable writing-system abilities with deterministic
graders.  Changing any prompt, expected value, threshold, or grader configuration
requires a new suite/case version (or an evaluator revision bump).
"""
from __future__ import annotations

from copy import deepcopy


SUITE_VERSION = "2"
PRODUCTION_ROLES = (
    "chapter_planner",
    "draft_writer",
    "review_agent",
    "state_extractor",
    "style_analyzer",
)

# Expensive qualification is intentionally limited to the five orthogonal
# abilities above.  Auxiliary production agents reuse the closest qualified
# ability instead of adding near-duplicate gateway calls to every evaluation.
# Keep these names aligned with ``agents.registry`` and the actual
# ``call_agent`` role names.
ROLE_EVIDENCE_ALIASES = {
    "outline_parser": "chapter_planner",
    "blank_planner": "chapter_planner",
    "local_rewrite_editor": "draft_writer",
    "drift_audit": "state_extractor",
    "query_planner": "chapter_planner",
    "evidence_ranker": "review_agent",
    "memory_compiler": "state_extractor",
}

ROUTABLE_ROLES = PRODUCTION_ROLES + tuple(ROLE_EVIDENCE_ALIASES)


def qualification_role_for(agent_role: str) -> str:
    """Return the directly tested ability that qualifies ``agent_role``."""

    return ROLE_EVIDENCE_ALIASES.get(agent_role, agent_role)


_SUITES: tuple[dict, ...] = (
    {
        "suite_key": "core-v2",
        "version": SUITE_VERSION,
        "name": "Core reasoning and knowledge boundaries",
        "purpose": "Synthetic causal, counterfactual, and epistemic-boundary checks",
        "target_role": None,
        "difficulty": "medium",
        "mode": "qualification",
        "pass_threshold": 0.70,
        "is_active": True,
        "is_private": False,
        "cases": [
            {
                "case_key": "core-causal-chain-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "causal_chain",
                "prompt_template": (
                    "档案规则：只有持有青印和白签的人能从内侧开启档案室；青印仍在室内，"
                    "白签昨夜被取走；门封完好。请只输出 JSON："
                    '{"outcome":"...","chain":["...","...","..."]}。'
                ),
                "expected_answer": (
                    '{"outcome":"inside_access_required",'
                    '"chain":["white_token_removed","seal_intact","opened_from_inside"]}'
                ),
                "grader_type": "ordered_json_facts",
                "grader_config": {
                    "field": "chain",
                    "required_order": ["white_token_removed", "seal_intact", "opened_from_inside"],
                    "exact_fields": {"outcome": "inside_access_required"},
                },
                "temperature": 0.0,
                "max_output_tokens": 256,
            },
            {
                "case_key": "core-counterfactual-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "counterfactual",
                "prompt_template": (
                    "规则：门仅在青印和白签同时存在时开启。现实中白签缺失，所以门未开。"
                    "反事实问题：若白签没有缺失且青印仍在，门是否开启？"
                    '只输出 JSON：{"opens":true或false,"because":[...] }。'
                ),
                "expected_answer": '{"opens":true,"because":["blue_seal","white_token"]}',
                "grader_type": "json_exact_fields",
                "grader_config": {
                    "exact_fields": {"opens": True},
                    "list_contains": {"because": ["blue_seal", "white_token"]},
                },
                "temperature": 0.0,
                "max_output_tokens": 192,
            },
            {
                "case_key": "core-knowledge-boundary-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "knowledge_boundary",
                "prompt_template": (
                    "事实：林澈独自把钥匙藏进北柜；顾禾在此之前离开，之后无人告诉顾禾。"
                    "问顾禾是否知道钥匙的确切位置。"
                    '只输出 JSON：{"answer":"known或unknown","may_infer":true或false}。'
                ),
                "expected_answer": '{"answer":"unknown","may_infer":false}',
                "grader_type": "json_exact_fields",
                "grader_config": {
                    "exact_fields": {"answer": "unknown", "may_infer": False},
                },
                "temperature": 0.0,
                "max_output_tokens": 192,
            },
        ],
    },
    {
        "suite_key": "planner-v2",
        "version": SUITE_VERSION,
        "name": "Chapter planner scene contracts",
        "purpose": "Scene contracts, required/forbidden beats, and causal ordering",
        "target_role": "chapter_planner",
        "difficulty": "hard",
        "mode": "qualification",
        "pass_threshold": 0.72,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "planner-contract-chain-v2",
                "case_version": SUITE_VERSION,
                "role": "chapter_planner",
                "category": "scene_contract",
                "prompt_template": (
                    "本章约束：先发现湿脚印，再核对门锁，最后才可怀疑守夜人；"
                    "守夜人本章不得认罪，主角不得打开密函。生成恰好 3 个 SceneContract。"
                    "只输出 JSON 数组；每项含 scene_type、goal、required_beats、forbidden_beats、"
                    "knowledge_delta、exit_state。"
                ),
                "expected_answer": "",
                "grader_type": "scene_contract",
                "grader_config": {
                    "exact_contracts": 3,
                    "required_keys": [
                        "scene_type", "goal", "required_beats", "forbidden_beats",
                        "knowledge_delta", "exit_state",
                    ],
                    "required_order": ["湿脚印", "核对门锁", "怀疑守夜人"],
                    "forbidden_substrings": ["守夜人认罪", "打开密函", "拆开密函"],
                },
                "temperature": 0.1,
                "max_output_tokens": 900,
            },
            {
                "case_key": "planner-knowledge-delta-v2",
                "case_version": SUITE_VERSION,
                "role": "chapter_planner",
                "category": "knowledge_boundary",
                "prompt_template": (
                    "已知：姜遥看见柜门水痕；陆简只听见钟声，未进入房间。"
                    "规划下一场时，分别列出两人可采取的行动和不可依据的秘密。"
                    '只输出 JSON：{"姜遥":{"can":[],"cannot":[]},"陆简":{"can":[],"cannot":[]}}。'
                ),
                "expected_answer": "",
                "grader_type": "planner_knowledge",
                "grader_config": {
                    "observer": "姜遥",
                    "outsider": "陆简",
                    "known_fact": "水痕",
                    "forbidden_fact": "水痕",
                },
                "temperature": 0.0,
                "max_output_tokens": 400,
            },
        ],
    },
    {
        "suite_key": "draft-v2",
        "version": SUITE_VERSION,
        "name": "Draft scene execution",
        "purpose": "POV, action beats, dialogue, subtext, prohibitions, and continuity",
        "target_role": "draft_writer",
        "difficulty": "hard",
        "mode": "qualification",
        "pass_threshold": 0.70,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "draft-subtext-scene-v2",
                "case_version": SUITE_VERSION,
                "role": "draft_writer",
                "category": "scene_execution",
                "prompt_template": (
                    "以姜遥单一限知视角写 180—260 字微场景。连续性：密函仍封口、桌边有新水痕。"
                    "必须出现动作‘擦去水痕’和‘把信封推回’，至少两句带引号对白，并用对白表现双方"
                    "都在回避守夜人的去向。不得拆信，不得写陆简内心，不得使用‘突然意识到’或‘不由得’。"
                ),
                "expected_answer": "",
                "grader_type": "draft_scene",
                "grader_config": {
                    "min_chars": 180,
                    "max_chars": 320,
                    "required_actions": ["擦去水痕", "把信封推回"],
                    "min_dialogue_lines": 2,
                    "subtext_anchors": ["守夜人", "去哪", "去向", "没见"],
                    "forbidden_substrings": ["拆开密函", "打开密函", "陆简心想", "突然意识到", "不由得"],
                },
                "temperature": 0.25,
                "max_output_tokens": 700,
            },
            {
                "case_key": "draft-continuity-v2",
                "case_version": SUITE_VERSION,
                "role": "draft_writer",
                "category": "continuity",
                "prompt_template": (
                    "写 120—220 字承接段。既有事实：铜灯已熄、窗闩从内侧扣住、宋霁不知道暗格。"
                    "必须让宋霁依据可见线索行动，但不得让她说出或找到暗格；至少包含一个动作节拍和一句对白。"
                ),
                "expected_answer": "",
                "grader_type": "continuity_scene",
                "grader_config": {
                    "min_chars": 120,
                    "required_facts": ["铜灯", "窗闩"],
                    "forbidden_knowledge": ["找到暗格", "暗格就在", "打开暗格"],
                    "requires_dialogue": True,
                },
                "temperature": 0.25,
                "max_output_tokens": 600,
            },
        ],
    },
    {
        "suite_key": "review-v2",
        "version": SUITE_VERSION,
        "name": "Editorial issue precision and recall",
        "purpose": "Gold issue detection with planted non-issues and false-positive control",
        "target_role": "review_agent",
        "difficulty": "hard",
        "mode": "qualification",
        "pass_threshold": 0.72,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "review-gold-f1-v2",
                "case_version": SUITE_VERSION,
                "role": "review_agent",
                "category": "issue_f1",
                "prompt_template": (
                    "审校片段：第一段写‘子时钟响后，姜遥才抵达’；第二段却写‘子时前一刻，"
                    "姜遥已在屋内看见密函内容’，而此前密函始终封口。她穿红衣是人物设定，不是错误。"
                    "只输出 JSON：issues 为问题 ID 数组，non_issues 为不应报错的 ID 数组。"
                    "可用 ID：time_order、knowledge_leak、red_clothes。"
                ),
                "expected_answer": "",
                "grader_type": "review_issue_f1",
                "grader_config": {
                    "gold_issues": ["time_order", "knowledge_leak"],
                    "gold_non_issues": ["red_clothes"],
                },
                "temperature": 0.0,
                "max_output_tokens": 300,
            },
            {
                "case_key": "review-clean-control-v2",
                "case_version": SUITE_VERSION,
                "role": "review_agent",
                "category": "false_positive_control",
                "prompt_template": (
                    "片段中人物先关门、后落闩、再熄灯；她只复述自己亲眼看到的事。"
                    "其中没有时序或知识边界错误。只输出 JSON："
                    '{"issues":[],"non_issues":["ordered_actions","knowledge_boundary"]}。'
                ),
                "expected_answer": "",
                "grader_type": "review_issue_f1",
                "grader_config": {
                    "gold_issues": [],
                    "gold_non_issues": ["ordered_actions", "knowledge_boundary"],
                },
                "temperature": 0.0,
                "max_output_tokens": 240,
            },
        ],
    },
    {
        "suite_key": "state-v2",
        "version": SUITE_VERSION,
        "name": "State extraction and epistemic boundaries",
        "purpose": "Strict JSON state, facts, and character knowledge",
        "target_role": "state_extractor",
        "difficulty": "hard",
        "mode": "qualification",
        "pass_threshold": 0.75,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "state-snapshot-v2",
                "case_version": SUITE_VERSION,
                "role": "state_extractor",
                "category": "state_schema",
                "prompt_template": (
                    "事实：姜遥在北柜发现一把湿钥匙；密函仍封口；陆简只知道钥匙存在，不知道北柜。"
                    "只输出 JSON，字段严格为 location、item、letter_opened、knowledge。"
                ),
                "expected_answer": (
                    '{"location":"北柜","item":"湿钥匙","letter_opened":false,'
                    '"knowledge":{"姜遥":["钥匙在北柜"],"陆简":["钥匙存在"]}}'
                ),
                "grader_type": "json_exact_fields",
                "grader_config": {
                    "exact_from_expected": True,
                    "reject_extra_keys": True,
                },
                "temperature": 0.0,
                "max_output_tokens": 350,
            },
            {
                "case_key": "state-event-delta-v2",
                "case_version": SUITE_VERSION,
                "role": "state_extractor",
                "category": "event_delta",
                "prompt_template": (
                    "旧状态：门=锁、灯=亮、知情者=[姜遥]。事件：姜遥开门后熄灯；陆简在门外只听见门响。"
                    "只输出 JSON：new_state、events、knowledge_delta 三个字段。"
                ),
                "expected_answer": "",
                "grader_type": "state_delta",
                "grader_config": {
                    "required_new_state": {"door": "open", "lamp": "off"},
                    "required_event_types": ["door_opened", "lamp_extinguished"],
                    "outsider": "陆简",
                    "forbidden_outsider_fact": "灯熄灭",
                },
                "temperature": 0.0,
                "max_output_tokens": 420,
            },
        ],
    },
    {
        "suite_key": "style-v2",
        "version": SUITE_VERSION,
        "name": "Style analysis and consistency",
        "purpose": "Generic statistical style analysis without author imitation",
        "target_role": "style_analyzer",
        "difficulty": "medium",
        "mode": "qualification",
        "pass_threshold": 0.70,
        "is_active": True,
        "is_private": False,
        "cases": [
            {
                "case_key": "style-metrics-v2",
                "case_version": SUITE_VERSION,
                "role": "style_analyzer",
                "category": "style_metrics",
                "prompt_template": (
                    "样本文本以第三人称限知叙述；共 10 句，其中 4 句对白；多数句子短于 20 字；"
                    "仅 1 处明喻。只输出 JSON：pov、dialogue_ratio、sentence_length_band、metaphor_density。"
                ),
                "expected_answer": (
                    '{"pov":"third_limited","dialogue_ratio":0.4,'
                    '"sentence_length_band":"short","metaphor_density":"low"}'
                ),
                "grader_type": "style_metrics",
                "grader_config": {
                    "expected": {
                        "pov": "third_limited",
                        "dialogue_ratio": 0.4,
                        "sentence_length_band": "short",
                        "metaphor_density": "low",
                    },
                    "numeric_tolerance": 0.05,
                },
                "temperature": 0.0,
                "max_output_tokens": 280,
            },
            {
                "case_key": "style-consistency-v2",
                "case_version": SUITE_VERSION,
                "role": "style_analyzer",
                "category": "consistency",
                "prompt_template": (
                    "基准：第三人称限知、短句、克制比喻。候选 A 改为第一人称长段抒情；"
                    "候选 B 保持第三人称限知与短句。只输出 JSON："
                    '{"more_consistent":"A或B","reasons":["..."]}。'
                ),
                "expected_answer": '{"more_consistent":"B","reasons":["pov","sentence_length"]}',
                "grader_type": "json_exact_fields",
                "grader_config": {
                    "exact_fields": {"more_consistent": "B"},
                    "list_contains": {"reasons": ["pov", "sentence_length"]},
                },
                "temperature": 0.0,
                "max_output_tokens": 220,
            },
        ],
    },
    {
        "suite_key": "context-v2",
        "version": SUITE_VERSION,
        "name": "Adaptive context robustness ladder",
        "purpose": "Position, multi-hop, instruction-retention, and belief-boundary checks",
        "target_role": None,
        "difficulty": "hard",
        "mode": "context_ladder",
        "pass_threshold": 0.80,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "context-position-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "position",
                "prompt_template": "Recall a planted four-digit archive code at the requested context position.",
                "expected_answer": "4471",
                "grader_type": "context_position",
                "grader_config": {},
                "temperature": 0.0,
                "max_output_tokens": 256,
            },
            {
                "case_key": "context-multihop-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "multihop",
                "prompt_template": "Combine the planted original code with the later reset event.",
                "expected_answer": "8820",
                "grader_type": "context_multihop",
                "grader_config": {},
                "temperature": 0.0,
                "max_output_tokens": 256,
            },
            {
                "case_key": "context-instruction-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "instruction",
                "prompt_template": "Retain the output-only JSON instruction across the synthetic context.",
                "expected_answer": "json_only",
                "grader_type": "context_instruction",
                "grader_config": {},
                "temperature": 0.0,
                "max_output_tokens": 256,
            },
            {
                "case_key": "context-belief-v2",
                "case_version": SUITE_VERSION,
                "role": None,
                "category": "belief",
                "prompt_template": "Prefer documented facts over a later unsupported distractor claim.",
                "expected_answer": "documented",
                "grader_type": "context_belief",
                "grader_config": {},
                "temperature": 0.0,
                "max_output_tokens": 256,
            },
        ],
    },
)


def v98_suite_definitions() -> list[dict]:
    """Return a defensive copy so callers cannot mutate the canonical bank."""

    return deepcopy(list(_SUITES))


V98_SUITE_KEYS = tuple(suite["suite_key"] for suite in _SUITES)
