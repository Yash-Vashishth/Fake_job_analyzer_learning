"""
Storage layer
=============
Persists every analyzed submission (email text, uploaded offer letter,
extracted domains, computed scores) plus optional human-verified labels
("this actually was a scam" / "this was legit") so the scoring weights
can be adaptively retrained from real outcomes over time (see learning.py).

Uses SQLite — zero external services required. Files (PDFs/images) are
written to disk under DATA_DIR/uploads and referenced by path; SQLite
itself stores structured fields + JSON blobs of the score breakdown.

NOTE on deployment: SQLite lives on local disk. On most PaaS free tiers
(Render, Railway, Fly.io) the filesystem is ephemeral unless you attach a
persistent volume — see the deployment notes in README_DEPLOY.md. Without
a volume, this data is wiped on every redeploy/restart.
"""

import os
import json
import sqlite3
import uuid
import shutil
import logging
import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "forensics.db")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

# Safety margin: stop writing new raw files (PDFs/images) once free space on
# the volume drops below this, so the disk never actually hits 0 bytes free
# (a fully-full disk can make SQLite itself fail to write, which is worse
# than just not storing this one file).
MIN_FREE_BYTES = 50 * 1024 * 1024  # 50MB

# If free space drops below this, automatically prune old, unlabeled raw
# files (keeping the scores/metadata — only the raw PDF/image bytes are
# deleted) to try to reclaim room before we start refusing new files.
AUTO_PRUNE_THRESHOLD_BYTES = 150 * 1024 * 1024  # 150MB
AUTO_PRUNE_MAX_AGE_DAYS = 30


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id              TEXT PRIMARY KEY,
                created_at      TEXT NOT NULL,
                email_text      TEXT,
                company_domain  TEXT,
                contact_domain  TEXT,
                mode            TEXT,
                file_type       TEXT,
                file_path       TEXT,
                original_filename TEXT,
                scores_json     TEXT NOT NULL,
                formula_json    TEXT NOT NULL,
                verdict_summary TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS labels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id   TEXT NOT NULL,
                label           TEXT NOT NULL CHECK(label IN ('scam','legit','unsure')),
                notes           TEXT,
                labeled_at      TEXT NOT NULL,
                FOREIGN KEY(submission_id) REFERENCES submissions(id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_labels_sub ON labels(submission_id)")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_submission(
    email_text: str,
    company_domain: str,
    contact_domain: str,
    mode: str,
    file_type: str,
    file_bytes: bytes,
    original_filename: str,
    all_scores: dict,
    formula_result: dict,
    verdict_summary: str,
) -> str:
    sub_id = str(uuid.uuid4())
    file_path = None
    storage_note = None

    if file_bytes and original_filename:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        free_bytes = shutil.disk_usage(DATA_DIR).free

        # Getting tight on space — try to reclaim room from old, unlabeled
        # raw files before deciding whether to store this one. Scores stay;
        # only the raw PDF/image bytes get freed.
        if free_bytes < AUTO_PRUNE_THRESHOLD_BYTES:
            try:
                freed = prune_old_files(max_age_days=AUTO_PRUNE_MAX_AGE_DAYS)["bytes_freed"]
                if freed:
                    logger.info(f"Auto-pruned {freed} bytes of old uploads to reclaim disk space")
                free_bytes = shutil.disk_usage(DATA_DIR).free
            except Exception as e:
                logger.warning(f"Auto-prune attempt failed: {e}")

        if free_bytes < MIN_FREE_BYTES:
            # Degrade gracefully: keep the analysis, scores, and email text
            # (small, structured data) but skip the raw file to avoid
            # driving the volume to 0 bytes free.
            storage_note = (
                f"Raw file not stored — volume has only {free_bytes // (1024*1024)}MB free "
                f"(safety margin is {MIN_FREE_BYTES // (1024*1024)}MB). Scores and text were still saved."
            )
            logger.warning(f"Submission {sub_id}: {storage_note}")
        else:
            safe_ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
            file_path = os.path.join(UPLOADS_DIR, f"{sub_id}.{safe_ext}")
            try:
                with open(file_path, "wb") as f:
                    f.write(file_bytes)
            except OSError as e:
                # Belt-and-braces: even with the pre-check above, a race
                # against other writes or a hard quota could still fail here.
                file_path = None
                storage_note = f"Raw file write failed ({e}); scores and text were still saved."
                logger.warning(f"Submission {sub_id}: {storage_note}")

    with _conn() as c:
        c.execute(
            """INSERT INTO submissions
               (id, created_at, email_text, company_domain, contact_domain, mode,
                file_type, file_path, original_filename, scores_json, formula_json, verdict_summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sub_id,
                datetime.datetime.utcnow().isoformat(),
                email_text,
                company_domain,
                contact_domain,
                mode,
                file_type,
                file_path,
                original_filename,
                json.dumps(all_scores),
                json.dumps(formula_result),
                verdict_summary,
            ),
        )
    return sub_id, storage_note


def add_label(submission_id: str, label: str, notes: str = "") -> dict:
    if label not in ("scam", "legit", "unsure"):
        raise ValueError("label must be one of: scam, legit, unsure")
    with _conn() as c:
        cur = c.execute(
            "SELECT id FROM submissions WHERE id = ?", (submission_id,)
        )
        if cur.fetchone() is None:
            raise KeyError(f"No submission with id {submission_id}")
        c.execute(
            "INSERT INTO labels (submission_id, label, notes, labeled_at) VALUES (?,?,?,?)",
            (submission_id, label, notes, datetime.datetime.utcnow().isoformat()),
        )
    return {"submission_id": submission_id, "label": label}


def get_submission(submission_id: str) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if row is None:
            return None
        labels = c.execute(
            "SELECT label, notes, labeled_at FROM labels WHERE submission_id = ? ORDER BY labeled_at DESC",
            (submission_id,),
        ).fetchall()
    result = dict(row)
    result["scores"] = json.loads(result.pop("scores_json"))
    result["formula"] = json.loads(result.pop("formula_json"))
    result["labels"] = [dict(l) for l in labels]
    return result


def get_history(limit: int = 50, offset: int = 0) -> list:
    with _conn() as c:
        rows = c.execute(
            """SELECT s.id, s.created_at, s.company_domain, s.contact_domain, s.mode,
                      s.file_type, s.original_filename, s.verdict_summary,
                      (SELECT label FROM labels l WHERE l.submission_id = s.id
                       ORDER BY l.labeled_at DESC LIMIT 1) AS latest_label
               FROM submissions s
               ORDER BY s.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_labeled_dataset() -> list:
    """
    Returns one row per labeled submission (most recent label wins),
    with full score breakdown attached — the input learning.py trains on.
    """
    with _conn() as c:
        rows = c.execute(
            """
            SELECT s.id, s.scores_json,
                   (SELECT label FROM labels l WHERE l.submission_id = s.id
                    ORDER BY l.labeled_at DESC LIMIT 1) AS label
            FROM submissions s
            WHERE EXISTS (SELECT 1 FROM labels l WHERE l.submission_id = s.id)
            """
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "label": r["label"],
            "scores": json.loads(r["scores_json"]),
        })
    return out


def get_storage_stats() -> dict:
    """
    Reports current disk usage for the volume this data lives on, plus a
    breakdown of what's using it. Use this to keep an eye on how close
    you are to filling a small volume (e.g. Railway's free 0.5GB default).
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    total, used, free = shutil.disk_usage(DATA_DIR)

    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    uploads_size = 0
    upload_count = 0
    if os.path.isdir(UPLOADS_DIR):
        for f in os.listdir(UPLOADS_DIR):
            fp = os.path.join(UPLOADS_DIR, f)
            if os.path.isfile(fp):
                uploads_size += os.path.getsize(fp)
                upload_count += 1

    with _conn() as c:
        submission_count = c.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"]
        label_count = c.execute("SELECT COUNT(*) AS n FROM labels").fetchone()["n"]

    return {
        "volume_total_bytes": total,
        "volume_used_bytes": used,
        "volume_free_bytes": free,
        "volume_free_percent": round(100 * free / total, 1) if total else None,
        "db_size_bytes": db_size,
        "uploads_size_bytes": uploads_size,
        "upload_count": upload_count,
        "submission_count": submission_count,
        "label_count": label_count,
        "min_free_safety_margin_bytes": MIN_FREE_BYTES,
        "auto_prune_threshold_bytes": AUTO_PRUNE_THRESHOLD_BYTES,
        "status": (
            "critical" if free < MIN_FREE_BYTES else
            "low" if free < AUTO_PRUNE_THRESHOLD_BYTES else
            "ok"
        ),
    }


def prune_old_files(max_age_days: int = 30, keep_labeled: bool = True) -> dict:
    """
    Deletes raw uploaded files (PDFs/images) older than max_age_days to
    free disk space. Does NOT delete the submission row, its scores, or
    its email text — only the raw binary, which is the large part. This
    keeps every submission usable for /retrain even after its file is gone.

    By default, labeled submissions (ones you've confirmed scam/legit via
    /flag) are kept regardless of age, since those are your most valuable
    training examples and you may want to re-examine the original file.
    """
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=max_age_days)).isoformat()

    with _conn() as c:
        if keep_labeled:
            rows = c.execute(
                """SELECT id, file_path FROM submissions
                   WHERE file_path IS NOT NULL AND created_at < ?
                   AND id NOT IN (SELECT DISTINCT submission_id FROM labels)""",
                (cutoff,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, file_path FROM submissions WHERE file_path IS NOT NULL AND created_at < ?",
                (cutoff,),
            ).fetchall()

        bytes_freed = 0
        pruned_ids = []
        for row in rows:
            fp = row["file_path"]
            if fp and os.path.exists(fp):
                try:
                    bytes_freed += os.path.getsize(fp)
                    os.remove(fp)
                    pruned_ids.append(row["id"])
                except OSError as e:
                    logger.warning(f"Failed to prune file for submission {row['id']}: {e}")

        if pruned_ids:
            c.executemany(
                "UPDATE submissions SET file_path = NULL WHERE id = ?",
                [(pid,) for pid in pruned_ids],
            )

    return {"pruned_count": len(pruned_ids), "bytes_freed": bytes_freed}
