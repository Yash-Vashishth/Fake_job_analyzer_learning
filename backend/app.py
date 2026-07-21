"""
Fake Job Offer Forensic Analyzer — Flask API
============================================
Endpoints:
  POST /analyze        → full forensic analysis
  GET  /weights        → get current default weights
  POST /weights        → update weights for a session
  GET  /health         → health check
"""

import os
import io
import json
import tempfile
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from forensics import typographic, signature, metadata, visual_layout, job_posting, domain_intel
import scorer
from scorer import compute
from llm_assist import enrich_scores
import storage
import learning

app = Flask(__name__)
# In production, set FRONTEND_ORIGIN to your deployed frontend URL (e.g.
# https://your-app.onrender.com) to restrict CORS. Defaults to "*" for
# local development.
CORS(app, origins=os.environ.get("FRONTEND_ORIGIN", "*"))

storage.init_db()

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "txt", "eml"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "2.0"})


@app.route("/weights", methods=["GET"])
def get_weights():
    return jsonify(scorer.DEFAULT_WEIGHTS)


@app.route("/analyze", methods=["POST"])
def analyze():
    # ── Parse inputs ──────────────────────────────────────────────────────
    email_text     = request.form.get("email_text", "").strip()
    company_domain = request.form.get("company_domain", "").strip()
    contact_domain = request.form.get("contact_domain", "").strip()
    mode           = request.form.get("mode", "research")
    weights_json   = request.form.get("weights", "{}")

    try:
        custom_weights = json.loads(weights_json)
    except Exception:
        custom_weights = {}

    uploaded_file = request.files.get("file")

    if not email_text and not uploaded_file and not company_domain:
        return jsonify({"error": "Provide at least one input: file, email text, or domain"}), 400

    # ── Route by input type ───────────────────────────────────────────────
    pdf_path   = None
    image_data = None
    file_type  = None
    text_for_analysis = email_text
    raw_file_bytes = None
    original_filename = None

    if uploaded_file and uploaded_file.filename:
        if not allowed_file(uploaded_file.filename):
            return jsonify({"error": "Unsupported file type"}), 400

        file_bytes = uploaded_file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            return jsonify({"error": "File too large (max 10MB)"}), 400

        raw_file_bytes = file_bytes
        original_filename = uploaded_file.filename

        ext = uploaded_file.filename.rsplit(".", 1)[1].lower()
        file_type = ext

        if ext == "pdf":
            # Save to temp file for PyMuPDF
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.write(file_bytes)
            tmp.close()
            pdf_path = tmp.name
        elif ext in ("jpg", "jpeg", "png"):
            image_data = file_bytes
        elif ext in ("txt", "eml"):
            text_for_analysis = file_bytes.decode("utf-8", errors="ignore")

    # ── Run all forensic modules ──────────────────────────────────────────
    all_scores = {}

    # 1. Typographic & glyph
    try:
        typo_scores = typographic.analyze(pdf_path=pdf_path, image_data=image_data)
    except Exception as e:
        typo_scores = _na_group(["glyph_sharpness","interglyph_spacing","baseline_jitter","font_renderer"], str(e))
    all_scores.update(typo_scores)

    # 2. Signature forensics
    try:
        sig_scores = signature.analyze(pdf_path=pdf_path, image_data=image_data)
    except Exception as e:
        sig_scores = _na_group(["ink_spread","edge_gaussian","dct_misalign","bg_texture"], str(e))
    all_scores.update(sig_scores)

    # 3. Temporal & metadata
    try:
        meta_scores = metadata.analyze(pdf_path=pdf_path, text_content=text_for_analysis)
    except Exception as e:
        meta_scores = _na_group(["causal_inversion","timezone_entropy","tool_anachronism"], str(e))
    all_scores.update(meta_scores)

    # 4. Visual & layout
    try:
        vis_scores = visual_layout.analyze(pdf_path=pdf_path, image_data=image_data)
    except Exception as e:
        vis_scores = _na_group(["logo_compression","seal_anomaly","letterhead_deviation","color_profile"], str(e))
    all_scores.update(vis_scores)

    # 5. Job posting authenticity (algorithmic base)
    try:
        job_scores = job_posting.analyze(
            text=text_for_analysis,
            company_domain=company_domain,
            contact_domain=contact_domain
        )
    except Exception as e:
        job_scores = _na_group(["domain_legitimacy","urgency_language","contact_verifiability","cross_platform"], str(e))
    all_scores.update(job_scores)

    # 6. Domain registration intelligence (age, privacy shielding, etc.)
    try:
        age_scores = domain_intel.analyze(
            company_domain=company_domain,
            contact_domain=contact_domain,
            text=text_for_analysis
        )
    except Exception as e:
        age_scores = _na_group(["domain_age"], str(e))
    all_scores.update(age_scores)

    # 7. LLM enrichment for semantic parameters
    try:
        all_scores, verdict_summary = enrich_scores(
            raw_scores=all_scores,
            text=text_for_analysis,
            company_domain=company_domain,
            contact_domain=contact_domain,
            mode=mode
        )
    except Exception as e:
        verdict_summary = f"LLM enrichment failed: {str(e)}"

    # 8. Run scoring formula
    formula_result = compute(all_scores, weights=custom_weights)

    # 9. Persist submission (email text, file, scores) for history + future learning
    submission_id = None
    storage_note = None
    try:
        submission_id, storage_note = storage.save_submission(
            email_text=email_text,
            company_domain=company_domain,
            contact_domain=contact_domain,
            mode=mode,
            file_type=file_type,
            file_bytes=raw_file_bytes,
            original_filename=original_filename,
            all_scores=all_scores,
            formula_result=formula_result,
            verdict_summary=verdict_summary,
        )
    except Exception as e:
        app.logger.warning(f"Failed to persist submission: {e}")
        storage_note = "Submission could not be saved to history (analysis result above is still valid)."

    # ── Cleanup temp file ─────────────────────────────────────────────────
    if pdf_path:
        try:
            os.unlink(pdf_path)
        except Exception:
            pass

    # ── Build response ────────────────────────────────────────────────────
    response = {
        "submission_id":   submission_id,
        "scores":          all_scores,
        "formula":         formula_result,
        "verdict_summary": verdict_summary,
        "input_type":      file_type or ("text" if text_for_analysis else "domain_only"),
    }
    if storage_note:
        response["storage_note"] = storage_note
    return jsonify(response)


@app.route("/history", methods=["GET"])
def history():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    return jsonify(storage.get_history(limit=limit, offset=offset))


@app.route("/submission/<submission_id>", methods=["GET"])
def get_submission(submission_id):
    record = storage.get_submission(submission_id)
    if record is None:
        return jsonify({"error": "Not found"}), 404
    # don't leak the raw filesystem path to the client
    record.pop("file_path", None)
    return jsonify(record)


@app.route("/flag", methods=["POST"])
def flag_submission():
    """
    Records a human-verified outcome for a past submission, e.g.:
    { "submission_id": "...", "label": "scam", "notes": "asked for 5000 rs registration fee, confirmed fake" }
    label must be one of: scam, legit, unsure
    """
    body = request.get_json(force=True, silent=True) or {}
    submission_id = body.get("submission_id")
    label = body.get("label")
    notes = body.get("notes", "")

    if not submission_id or not label:
        return jsonify({"error": "submission_id and label are required"}), 400

    try:
        result = storage.add_label(submission_id, label, notes)
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Check whether enough new labels have accumulated to justify an
    # automatic retrain — no manual /retrain call needed for this to work
    # over time, though it's still there if you want to force it.
    try:
        auto_result = learning.maybe_auto_retrain()
        if auto_result.get("triggered"):
            scorer.reload_weights()
        result["auto_retrain"] = auto_result
    except Exception as e:
        app.logger.warning(f"Auto-retrain check failed: {e}")

    return jsonify(result)


@app.route("/learning/status", methods=["GET"])
def learning_status():
    return jsonify(learning.current_status())


@app.route("/retrain", methods=["POST"])
def retrain():
    """
    Recomputes parameter weights from all labeled submissions collected so
    far via /flag, and hot-reloads them into the running scorer. Safe to
    call repeatedly; parameters without enough labeled data simply keep
    their hardcoded weight. Note: /flag also triggers this automatically
    once enough new labeled data accumulates — this endpoint is for
    forcing it on demand.
    """
    result = learning.retrain()
    scorer.reload_weights()
    return jsonify(result)


@app.route("/storage/status", methods=["GET"])
def storage_status():
    """
    Reports disk usage for the data volume: how full it is, what's using
    the space (DB vs raw uploaded files), and whether auto-pruning has
    kicked in. Check this periodically, especially on a small volume.
    """
    return jsonify(storage.get_storage_stats())


@app.route("/storage/prune", methods=["POST"])
def storage_prune():
    """
    Manually deletes raw uploaded files (PDFs/images) older than
    max_age_days to free disk space. Submission rows, scores, and email
    text are kept regardless — only the large binary files are removed,
    and only for unlabeled submissions unless keep_labeled=false is passed.
    This also runs automatically inside /analyze once free space drops
    below a threshold, so manual calls are mostly for reclaiming space
    proactively or right before you know a burst of uploads is coming.
    """
    body = request.get_json(force=True, silent=True) or {}
    max_age_days = int(body.get("max_age_days", 30))
    keep_labeled = bool(body.get("keep_labeled", True))
    result = storage.prune_old_files(max_age_days=max_age_days, keep_labeled=keep_labeled)
    return jsonify(result)


def _na_group(keys: list, reason: str) -> dict:
    return {k: {"score": None, "reason": f"Module error: {reason}", "applicable": False} for k in keys}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
