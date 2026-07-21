"""
LLM-Assisted Scoring via Anthropic API
=======================================
Handles parameters that need semantic understanding:
- Urgency language (when no text extraction possible)
- Contact verifiability enrichment
- Cross-platform consistency reasoning
- Overall verdict summary generation
"""

import os
import json
import re
import anthropic

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=API_KEY) if API_KEY else None


def enrich_scores(
    raw_scores: dict,
    text: str = "",
    company_domain: str = "",
    contact_domain: str = "",
    mode: str = "research"
) -> dict:
    """
    Takes raw algorithmic scores and enriches semantic parameters
    using the LLM. Also generates the final verdict summary.
    Returns updated scores dict + verdict_summary.

    If no ANTHROPIC_API_KEY is configured, skips the network call
    immediately and returns the algorithmic scores as-is (already
    computed by forensics/job_posting.py) plus a generic summary —
    the app is fully usable without a key, just without LLM-refined
    nuance on urgency_language / contact_verifiability / cross_platform.
    """
    if client is None:
        return raw_scores, _algo_only_summary(raw_scores)

    if not text and not company_domain and not contact_domain:
        return raw_scores, "Insufficient text input for LLM-assisted analysis."

    depth = (
        "2 sentences with specific forensic signal references"
        if mode == "research"
        else "1 concise sentence with the key signal"
    )

    # Build context summary of what algorithmic analysis already found
    algo_summary = _build_algo_summary(raw_scores)

    prompt = f"""You are a senior forensic analyst specializing in fake job offer detection in India.
Algorithmic forensic analysis has already been run on this document. Your role is to:
1. Assess semantic/text-based parameters the algorithm cannot fully measure
2. Generate an overall verdict summary

ALGORITHMIC RESULTS ALREADY COMPUTED:
{algo_summary}

TEXT CONTENT TO ANALYZE:
{text[:3000] if text else "[No text provided]"}

COMPANY DOMAIN: {company_domain or "not provided"}
CONTACT DOMAIN: {contact_domain or "not provided"}

Assess ONLY these semantic parameters (score 0-10, higher = more legitimate):

- urgency_language: Look for payment requests ("registration fee", "processing fee", UPI/Paytm requests), 
  extreme pressure ("join within 24 hours", "limited seats"), unrealistic salary promises, 
  "no interview needed". Score 0-1 if payment request found. 0-3 if multiple pressure tactics.
  India context: "refundable deposit" is always fraud.

- contact_verifiability: Check for: landline number (+91 STD code format), named HR person 
  (first + last name), CIN/GST number, physical office address, corporate email.
  Deduct heavily for mobile-only, Gmail/Yahoo, no named person.

- cross_platform: Does text mention job portals (Naukri, LinkedIn, Indeed, Shine, Foundit)?
  Does it reference company careers page? Any employee referral chain?

Respond ONLY with valid JSON (no markdown, no backticks, no explanation):
{{
  "urgency_language": {{
    "score": <0-10>,
    "reason": "<{depth}>"
  }},
  "contact_verifiability": {{
    "score": <0-10>,
    "reason": "<{depth}>"
  }},
  "cross_platform": {{
    "score": <0-10>,
    "reason": "<{depth}>"
  }},
  "verdict_summary": "<3 sentences: overall risk assessment, strongest fraud signals found, recommended action for India context>"
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(raw)

        verdict_summary = parsed.pop("verdict_summary", "Analysis complete.")

        # Merge LLM scores into raw_scores (LLM overrides for semantic params)
        for param_id, data in parsed.items():
            if param_id in raw_scores:
                # Blend: average with algorithmic if both available
                algo_score = raw_scores[param_id].get("score")
                llm_score = data.get("score")
                if algo_score is not None and llm_score is not None:
                    blended = round((algo_score + llm_score) / 2, 1)
                    raw_scores[param_id]["score"] = blended
                    raw_scores[param_id]["reason"] = (
                        f"[Algo: {algo_score}/10] {raw_scores[param_id]['reason']} | "
                        f"[LLM: {llm_score}/10] {data['reason']}"
                    )
                else:
                    raw_scores[param_id]["score"] = llm_score
                    raw_scores[param_id]["reason"] = data["reason"]
                raw_scores[param_id]["applicable"] = True
            else:
                raw_scores[param_id] = {**data, "applicable": True}

        return raw_scores, verdict_summary

    except Exception as e:
        return raw_scores, f"LLM enrichment unavailable ({str(e)}); algorithmic scores only."


def _algo_only_summary(scores: dict) -> str:
    applicable = {k: v for k, v in scores.items() if v.get("applicable") and v.get("score") is not None}
    if not applicable:
        return "No ANTHROPIC_API_KEY configured; no algorithmic signals available either."
    weakest = sorted(applicable.items(), key=lambda kv: kv[1]["score"])[:3]
    flags = ", ".join(f"{k} ({v['score']}/10)" for k, v in weakest)
    return (
        f"No ANTHROPIC_API_KEY configured — algorithmic scores only. "
        f"Lowest-scoring signals: {flags}. See per-parameter reasons and the final formula for detail."
    )


def _build_algo_summary(scores: dict) -> str:
    lines = []
    for param_id, s in scores.items():
        if s.get("applicable") and s.get("score") is not None:
            lines.append(f"  {param_id}: {s['score']}/10 — {s['reason'][:100]}")
    return "\n".join(lines) if lines else "  No algorithmic scores available"
