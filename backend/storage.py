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
import datetime
from contextlib import contextmanager

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "forensics.db")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")


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

    if file_bytes and original_filename:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        safe_ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
        file_path = os.path.join(UPLOADS_DIR, f"{sub_id}.{safe_ext}")
        with open(file_path, "wb") as f:
            f.write(file_bytes)

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
    return sub_id


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
