"""
Scoring Formula Engine
======================
FinalScore = clamp(WeightedBase + BoosterPenalties, 0, 100)

WeightedBase:
  Each active parameter contributes:
    fraud_contribution(p) = (1 - score/10) * weight(p)
  WeightedBase = (Σ fraud_contribution) / (Σ active_weights) * 100

BoosterPenalties (additive, fire independently of weighted average):
  Hard gates  → weight * 1.2  (when score ≤ HARD_THRESHOLD)
  Mid boosters→ weight * 0.8  (when score ≤ BOOST_THRESHOLD)
"""

import os
import json
from typing import Dict, Any

# Default weights (1–20 scale)
HARDCODED_WEIGHTS: Dict[str, int] = {
    # Typographic & glyph
    "glyph_sharpness":     6,
    "interglyph_spacing":  8,
    "baseline_jitter":     7,
    "font_renderer":       9,
    # Signature forensics
    "ink_spread":          6,
    "edge_gaussian":       7,
    "dct_misalign":        9,
    "bg_texture":          8,
    # Temporal & metadata
    "causal_inversion":    15,
    "timezone_entropy":    4,
    "tool_anachronism":    15,
    # Visual & layout
    "logo_compression":    5,
    "seal_anomaly":        7,
    "letterhead_deviation":9,
    "color_profile":       4,
    # Job posting authenticity
    "domain_legitimacy":   15,
    "urgency_language":    10,
    "contact_verifiability":9,
    "cross_platform":      5,
    "domain_age":          12,
}

LEARNED_WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "data", "learned_weights.json")


def _load_effective_weights() -> Dict[str, int]:
    """
    Starts from the hardcoded, hand-tuned weights and overlays any weights
    the /retrain endpoint has learned from labeled submission history.
    Missing file / bad file / any error => fall back to hardcoded only.
    """
    weights = dict(HARDCODED_WEIGHTS)
    try:
        if os.path.exists(LEARNED_WEIGHTS_PATH):
            with open(LEARNED_WEIGHTS_PATH, "r") as f:
                learned = json.load(f)
            for k, v in learned.get("weights", {}).items():
                if k in weights:
                    weights[k] = v
    except Exception:
        pass
    return weights


# DEFAULT_WEIGHTS is recomputed at import time; call reload_weights() after
# /retrain updates the file so a running process picks up new weights
# without a restart.
DEFAULT_WEIGHTS: Dict[str, int] = _load_effective_weights()


def reload_weights() -> Dict[str, int]:
    global DEFAULT_WEIGHTS
    DEFAULT_WEIGHTS = _load_effective_weights()
    return DEFAULT_WEIGHTS


# Parameter types
HARD_GATES = {"causal_inversion", "tool_anachronism", "domain_legitimacy"}
BOOSTERS   = {"urgency_language", "domain_age"}

HARD_THRESHOLD  = 2   # score ≤ this → hard gate fires
BOOST_THRESHOLD = 3   # score ≤ this → booster fires


def compute(
    scores: Dict[str, Dict[str, Any]],
    weights: Dict[str, int] = None
) -> Dict[str, Any]:

    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    weighted_num  = 0.0
    weighted_den  = 0.0
    booster_total = 0
    trace         = []
    fired_gates   = []

    for param_id, s in scores.items():
        if not s.get("applicable", True) or s.get("score") is None:
            trace.append({
                "param": param_id,
                "score": None,
                "kind": "n/a",
                "contribution": "skipped (not applicable)"
            })
            continue

        score  = s["score"]
        weight = w.get(param_id, 5)
        fraud_contrib = (1.0 - score / 10.0) * weight

        if param_id in HARD_GATES and score <= HARD_THRESHOLD:
            penalty = round(weight * 1.2)
            booster_total += penalty
            fired_gates.append(param_id)
            trace.append({
                "param": param_id,
                "score": score,
                "weight": weight,
                "kind": "hard-gate fired",
                "contribution": f"+{penalty} direct penalty"
            })

        elif param_id in BOOSTERS and score <= BOOST_THRESHOLD:
            penalty = round(weight * 0.8)
            booster_total += penalty
            weighted_num  += fraud_contrib
            weighted_den  += weight
            trace.append({
                "param": param_id,
                "score": score,
                "weight": weight,
                "kind": "booster fired + weighted",
                "contribution": f"{fraud_contrib:.2f} weighted + +{penalty} boost"
            })

        else:
            weighted_num += fraud_contrib
            weighted_den += weight
            trace.append({
                "param": param_id,
                "score": score,
                "weight": weight,
                "kind": "weighted",
                "contribution": f"{fraud_contrib:.2f} pts"
            })

    base  = round((weighted_num / weighted_den * 100) if weighted_den > 0 else 0)
    final = int(min(100, max(0, base + booster_total)))

    verdict = (
        "HIGH_RISK"   if final >= 60 else
        "MEDIUM_RISK" if final >= 35 else
        "LOW_RISK"
    )

    # Category-level sub-scores
    cat_scores = _category_scores(scores)

    return {
        "final_score":     final,
        "base_score":      base,
        "booster_total":   booster_total,
        "verdict":         verdict,
        "fired_gates":     fired_gates,
        "trace":           trace,
        "category_scores": cat_scores,
        "formula": (
            f"WeightedBase={base}/100 (Σ fraud_contrib/Σ weights × 100) "
            f"+ Boosters={booster_total} "
            f"= clamp({base}+{booster_total}, 0, 100) = {final}"
        )
    }


def _category_scores(scores: Dict[str, Dict]) -> Dict[str, float]:
    categories = {
        "Typographic & glyph":        ["glyph_sharpness","interglyph_spacing","baseline_jitter","font_renderer"],
        "Signature forensics":         ["ink_spread","edge_gaussian","dct_misalign","bg_texture"],
        "Temporal & metadata":         ["causal_inversion","timezone_entropy","tool_anachronism"],
        "Visual & layout":             ["logo_compression","seal_anomaly","letterhead_deviation","color_profile"],
        "Job posting authenticity":    ["domain_legitimacy","urgency_language","contact_verifiability","cross_platform","domain_age"],
    }
    result = {}
    for cat, params in categories.items():
        active = [
            scores[p]["score"]
            for p in params
            if p in scores and scores[p].get("applicable", True) and scores[p].get("score") is not None
        ]
        result[cat] = round(sum(active) / len(active), 2) if active else None
    return result
