"""v9.0 Appraisal & Affect engine (spec §9–13, §20.3).

Deterministic, explainable, CPU-only. Results are *narrative simulation
parameters*, never psychological truth.

Pipeline:
    Event → Appraisal → target affect → integrate (decay+shock) →
    derived emotion label → expression constraints
"""
from __future__ import annotations

import math
from typing import Any

from app.contracts.narrative import (
    AffectTransition,
    AgencyVector,
    CharacterAppraisal,
    ExpressionConstraint,
    VAD,
)
from app.engine.narrative_state import (
    affect_profile,
    affect_vad,
    active_goals,
    normalize_state,
    set_affect_vad,
)

SHOCK_IMPULSES: dict[str, float] = {
    "none": 0.0,
    "surprise": 0.25,
    "goal_damage": 0.35,
    "attachment_threat": 0.40,
    "physical_threat": 0.45,
    "betrayal_reveal": 0.50,
}


class AppraisalEngine:
    def __init__(self, config: dict[str, Any] | None = None):
        from app.engine.cognitive_config import DEFAULT_CAUSAL_CONFIG

        self.cfg = {**DEFAULT_CAUSAL_CONFIG, **(config or {})}

    # ── appraisal completion (spec §10) ───────────────────────────

    def compute_appraisal(
        self,
        character_id: str,
        event_key: str | None,
        event_features: dict[str, Any],
        state: dict[str, Any],
    ) -> CharacterAppraisal:
        """Complete/derive an appraisal vector from event features + state.

        Event features (from planner proposal or extraction):
            goal_congruence, novelty, certainty, controllability,
            agency_self/other/circumstance, norm_violation,
            relationship_weight, is_public, physical_threat, autonomy_threat,
            attachment_threat
        """
        normalized = normalize_state(state)
        profile = affect_profile(normalized)
        feats = event_features or {}

        goal_congruence = _clamp(feats.get("goal_congruence", 0.0), -1.0, 1.0)
        novelty = _clamp(feats.get("novelty", 0.3), 0.0, 1.0)
        certainty = _clamp(feats.get("certainty", 0.5), 0.0, 1.0)
        controllability = _clamp(feats.get("controllability", 0.5), 0.0, 1.0)
        norm_violation = _clamp(feats.get("norm_violation", 0.0), -1.0, 1.0)
        relationship_weight = _clamp(feats.get("relationship_weight", 0.3), 0.0, 1.0)

        # Threat bias raises perceived threat dimensions (spec §11.2)
        bias = profile["threat_bias"]
        autonomy_threat = _clamp(
            feats.get("autonomy_threat", 0.0) + 0.15 * goal_congruence * -1 * bias, 0.0, 1.0
        )
        attachment_threat = _clamp(
            feats.get("attachment_threat", 0.0)
            + 0.2 * relationship_weight * profile["attachment_sensitivity"],
            0.0,
            1.0,
        )
        physical_threat = _clamp(feats.get("physical_threat", 0.0), 0.0, 1.0)

        # Goal relevance: stronger when active goals exist for this character
        goals = active_goals(normalized)
        goal_relevance = _clamp(
            feats.get("goal_relevance", 0.5 if goals else 0.2), 0.0, 1.0
        )

        agency = feats.get("agency") or {}
        norm_compat = _clamp(
            -abs(norm_violation) * (1 if norm_violation >= 0 else -1)
            if norm_violation
            else feats.get("norm_compatibility", 0.0),
            -1.0,
            1.0,
        )
        if feats.get("norm_compatibility") is not None:
            norm_compat = _clamp(feats["norm_compatibility"], -1.0, 1.0)

        information_gain = _clamp(
            feats.get("information_gain", novelty * certainty), 0.0, 1.0
        )

        return CharacterAppraisal(
            character_id=character_id,
            event_key=event_key,
            goal_relevance=goal_relevance,
            goal_congruence=goal_congruence,
            novelty=novelty,
            certainty=certainty,
            controllability=controllability,
            agency=AgencyVector(
                self_=_clamp(agency.get("self", 0.0), 0.0, 1.0),
                other=_clamp(agency.get("other", 0.5), 0.0, 1.0),
                circumstance=_clamp(agency.get("circumstance", 0.3), 0.0, 1.0),
            ),
            norm_compatibility=norm_compat,
            relationship_weight=relationship_weight,
            information_gain=information_gain,
            autonomy_threat=autonomy_threat,
            attachment_threat=max(attachment_threat, physical_threat * 0.5),
        )

    # ── target affect (spec §11.1) ────────────────────────────────

    def compute_target_affect(
        self, appraisal: CharacterAppraisal, state: dict[str, Any]
    ) -> VAD:
        """F(appraisal, core, goal, relationship, physical) → target VAD.

        Explainable linear composition; each term's weight documented inline.
        """
        profile = affect_profile(normalize_state(state))
        a = appraisal

        # Valence: congruence with goals + norm compatibility + threats (negative)
        valence = (
            0.45 * a.goal_congruence * max(a.goal_relevance, 0.3)
            + 0.20 * a.norm_compatibility
            - 0.15 * a.autonomy_threat
            - 0.20 * a.attachment_threat * profile["attachment_sensitivity"]
        )
        # Uncertain events pull valence slightly negative (threat bias)
        valence -= 0.08 * (1.0 - a.certainty) * profile["threat_bias"]

        # Arousal: novelty + relevance + threats, scaled by reactivity
        arousal = (
            0.30 * a.novelty
            + 0.25 * a.goal_relevance
            + 0.20 * a.autonomy_threat
            + 0.15 * a.attachment_threat
        ) * (0.6 + 0.8 * profile["reactivity"])

        # Dominance: controllability - other-agency - threats
        dominance = (
            0.40 * a.controllability
            - 0.25 * a.agency.other
            - 0.20 * a.autonomy_threat
            - 0.15 * a.attachment_threat
        )

        return VAD(
            valence=round(_clamp(valence, -1.0, 1.0), 4),
            arousal=round(_clamp(arousal, 0.0, 1.0), 4),
            dominance=round(_clamp(dominance, -1.0, 1.0), 4),
        )

    # ── affect integration (spec §11.1 decay model) ───────────────

    def integrate_affect(
        self,
        state: dict[str, Any],
        target: VAD,
        *,
        shock: str = "none",
        delta_chapters: float = 1.0,
    ) -> VAD:
        """next = clip(decay*prev + (1-decay)*target + shock_impulse)."""
        profile = affect_profile(normalize_state(state))
        prev = _vad_from_tuple(affect_vad(normalize_state(state)))

        tau = max(0.5, float(profile.get("recovery_tau", 4.0)))
        dt = max(0.0, float(delta_chapters))
        decay = math.exp(-dt / tau)

        impulse = SHOCK_IMPULSES.get(shock, 0.0) * profile["reactivity"]

        next_v = _clamp(decay * prev.valence + (1 - decay) * target.valence, -1.0, 1.0)
        next_a = _clamp(decay * prev.arousal + (1 - decay) * target.arousal + impulse, 0.0, 1.0)
        next_d = _clamp(decay * prev.dominance + (1 - decay) * target.dominance, -1.0, 1.0)
        return VAD(valence=round(next_v, 4), arousal=round(next_a, 4), dominance=round(next_d, 4))

    # ── emotion label derivation (spec §9, CC-09) ─────────────────

    def derive_emotion_labels(self, appraisal: CharacterAppraisal, vad: VAD) -> list[str]:
        """Labels are derived results, never the sole state source."""
        labels: list[str] = []
        a = appraisal
        if a.novelty > 0.7 and vad.arousal > 0.5:
            labels.append("surprise" if vad.valence >= 0 else "alarm")
        if vad.valence < -0.15 and vad.arousal > 0.45:
            if a.agency.other > 0.6 and a.norm_compatibility < -0.3:
                labels.append("indignation")
            elif a.agency.other > 0.6:
                labels.append("anger")
            else:
                labels.append("anxiety" if vad.dominance < 0 else "distress")
        elif vad.valence < -0.15 and vad.arousal <= 0.45:
            labels.append("sadness" if a.goal_relevance > 0.5 else "melancholy")
        elif vad.valence > 0.2:
            labels.append("joy" if vad.arousal > 0.5 else "contentment")
        if a.attachment_threat > 0.6 and vad.valence < 0:
            labels.append("attachment_fear")
        if a.autonomy_threat > 0.6:
            labels.append("resentment")
        if a.certainty < 0.35 and a.information_gain > 0.5:
            labels.append("suspicion")
        return labels[:3]

    # ── expression constraints (spec §13, CC-10) ──────────────────

    def derive_expression_constraints(
        self, character_id: str, vad: VAD, state: dict[str, Any]
    ) -> ExpressionConstraint:
        """Convert affect + profile into *tendencies*, never body cues."""
        profile = affect_profile(normalize_state(state))
        suppression = profile["suppression"]
        arousal = vad.arousal

        # High suppression pushes visible signs down, internal tension up
        visibility = _level(arousal * (1.0 - 0.7 * suppression) * (1.0 + max(0.0, -vad.valence)))
        motor_tension = _level(arousal * (0.5 + 0.5 * (1.0 - suppression)))
        speech_control = _invert_level(_level(arousal * (1.0 - suppression)))
        speech_rate = (
            "faster"
            if arousal > 0.6 and suppression < 0.5
            else ("slower" if arousal > 0.6 and suppression >= 0.7 else "normal")
        )
        attention_narrowing = _level(arousal * 0.9)
        approach = _level(max(0.0, vad.valence) * (0.5 + vad.dominance))
        avoidance = _level(max(0.0, -vad.valence) * (1.0 - 0.5 * vad.dominance))
        aggression = (
            "high"
            if arousal > 0.7 and vad.valence < -0.2 and vad.dominance > 0.1 and suppression < 0.4
            else ("contained" if suppression > 0.7 else "medium")
        )

        return ExpressionConstraint(
            character_id=character_id,
            visibility=visibility,
            motor_tension=motor_tension,
            speech_control=speech_control,
            speech_rate=speech_rate,
            attention_narrowing=attention_narrowing,
            approach_tendency=approach,
            avoidance_tendency=avoidance,
            aggression_tendency=aggression,
        )

    # ── full affect pipeline for one transition ───────────────────

    def build_affect_transition(
        self,
        character_id: str,
        appraisal: CharacterAppraisal,
        state: dict[str, Any],
        *,
        cause_event_keys: list[str] | None = None,
        shock: str = "none",
        shock_event_key: str | None = None,
        delta_chapters: float = 1.0,
    ) -> AffectTransition:
        normalized = normalize_state(state)
        prev_vad = _vad_from_tuple(affect_vad(normalized))
        target = self.compute_target_affect(appraisal, normalized)
        next_vad = self.integrate_affect(
            normalized, target, shock=shock, delta_chapters=delta_chapters
        )
        labels = self.derive_emotion_labels(appraisal, next_vad)
        return AffectTransition(
            character_id=character_id,
            from_vad=prev_vad,
            to_vad=next_vad,
            cause_event_keys=cause_event_keys or [],
            shock=shock,  # type: ignore[arg-type]
            shock_event_key=shock_event_key,
            derived_emotions=labels,
        )

    def apply_transition(self, state: dict[str, Any], transition: AffectTransition) -> dict[str, Any]:
        """Write the integrated VAD back into a cloned state (runtime only)."""
        out = normalize_state(state)
        set_affect_vad(out, transition.to_vad.valence, transition.to_vad.arousal, transition.to_vad.dominance)
        if transition.derived_emotions:
            affect = out.setdefault("affect", {})
            affect["active_emotions"] = [
                {"type": lbl, "intensity": round(transition.to_vad.arousal, 3)}
                for lbl in transition.derived_emotions
            ]
        return out


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = 0.0
    return max(lo, min(hi, x))


def _vad_from_tuple(t: Any) -> VAD:
    if isinstance(t, VAD):
        return t
    if isinstance(t, dict):
        return VAD.model_validate(t)
    if isinstance(t, (list, tuple)) and len(t) >= 3:
        return VAD(valence=t[0], arousal=t[1], dominance=t[2])
    return VAD()


def _level(v: float) -> str:
    if v >= 0.62:
        return "high"
    if v >= 0.32:
        return "medium"
    return "low"


def _invert_level(level: str) -> str:
    return {"low": "high", "medium": "medium", "high": "low"}.get(level, "medium")
