"""
Adaptive Weight Learning
========================
Turns human-verified labels ("this was actually a scam" / "this was
legit") into updated parameter weights for scorer.py.

Approach (deliberately simple + explainable, not a black box):
  For each parameter, look at every labeled submission where that
  parameter was applicable. Compute the point-biserial correlation
  between the parameter's fraud_contribution ((10 - score) / 10) and
  the binary outcome (1 = scam, 0 = legit).

  A parameter that reliably moves toward "fraud" on real scams and
  toward "clean" on real legit offers gets correlation near +1 and is
  up-weighted. A parameter that doesn't discriminate at all (random
  noise relative to the label) gets correlation near 0 and is
  down-weighted toward a floor. A parameter that's *inversely*
  correlated with outcome (a bug, or a heuristic that doesn't hold up
  in your real data) gets pulled toward the floor too, rather than
  flipped negative.

  weight = floor + max(0, correlation) * (ceiling - floor)

Requires a minimum sample size per parameter (with both classes
present) before trusting it — otherwise the hardcoded weight is kept
as-is. This guards against overfitting to 3 labeled examples.
"""

import json
import os
import datetime
from typing import Dict, List

import numpy as np

import storage
from scorer import HARDCODED_WEIGHTS

MIN_SAMPLES_PER_PARAM = 12
MIN_SAMPLES_PER_CLASS = 4
WEIGHT_FLOOR = 1
WEIGHT_CEILING = 20

LEARNED_WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "data", "learned_weights.json")


def retrain() -> dict:
    dataset = storage.get_labeled_dataset()

    # label -> 1 for scam, 0 for legit; drop "unsure"
    usable = [d for d in dataset if d["label"] in ("scam", "legit")]

    per_param_scores: Dict[str, List[float]] = {}
    per_param_labels: Dict[str, List[int]] = {}

    for row in usable:
        y = 1 if row["label"] == "scam" else 0
        for param_id, s in row["scores"].items():
            if not s.get("applicable", True) or s.get("score") is None:
                continue
            fraud_contrib = (10 - s["score"]) / 10.0
            per_param_scores.setdefault(param_id, []).append(fraud_contrib)
            per_param_labels.setdefault(param_id, []).append(y)

    new_weights = dict(HARDCODED_WEIGHTS)
    per_param_report = {}

    for param_id, hardcoded_w in HARDCODED_WEIGHTS.items():
        xs = per_param_scores.get(param_id, [])
        ys = per_param_labels.get(param_id, [])
        n = len(xs)
        n_pos = sum(ys)
        n_neg = n - n_pos

        if n < MIN_SAMPLES_PER_PARAM or n_pos < MIN_SAMPLES_PER_CLASS or n_neg < MIN_SAMPLES_PER_CLASS:
            per_param_report[param_id] = {
                "status": "kept_hardcoded",
                "reason": f"insufficient labeled data (n={n}, scam={n_pos}, legit={n_neg}; need >={MIN_SAMPLES_PER_PARAM} total and >={MIN_SAMPLES_PER_CLASS} per class)",
                "weight": hardcoded_w,
            }
            continue

        xs_arr = np.array(xs, dtype=float)
        ys_arr = np.array(ys, dtype=float)

        if np.std(xs_arr) == 0 or np.std(ys_arr) == 0:
            corr = 0.0
        else:
            corr = float(np.corrcoef(xs_arr, ys_arr)[0, 1])
            if np.isnan(corr):
                corr = 0.0

        learned_w = round(WEIGHT_FLOOR + max(0.0, corr) * (WEIGHT_CEILING - WEIGHT_FLOOR))
        new_weights[param_id] = learned_w
        per_param_report[param_id] = {
            "status": "learned",
            "correlation": round(corr, 3),
            "n_samples": n,
            "n_scam": n_pos,
            "n_legit": n_neg,
            "hardcoded_weight": hardcoded_w,
            "learned_weight": learned_w,
        }

    output = {
        "trained_at": datetime.datetime.utcnow().isoformat(),
        "total_labeled_submissions": len(usable),
        "weights": new_weights,
        "report": per_param_report,
    }

    os.makedirs(os.path.dirname(LEARNED_WEIGHTS_PATH), exist_ok=True)
    with open(LEARNED_WEIGHTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    return output


def current_status() -> dict:
    dataset = storage.get_labeled_dataset()
    usable = [d for d in dataset if d["label"] in ("scam", "legit")]
    learned_exists = os.path.exists(LEARNED_WEIGHTS_PATH)
    last_trained = None
    if learned_exists:
        with open(LEARNED_WEIGHTS_PATH) as f:
            last_trained = json.load(f).get("trained_at")
    return {
        "total_labeled_submissions": len(usable),
        "scam_count": sum(1 for d in usable if d["label"] == "scam"),
        "legit_count": sum(1 for d in usable if d["label"] == "legit"),
        "min_required_per_param": MIN_SAMPLES_PER_PARAM,
        "has_learned_weights": learned_exists,
        "last_trained_at": last_trained,
    }
