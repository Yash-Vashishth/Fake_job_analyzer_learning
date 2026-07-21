"""
Domain Registration Intelligence
=================================
Adds a "domain_age" forensic parameter: when was the domain that sent/hosts
the offer registered, and does its registration profile look temporary
(freshly-registered, single-year registration, privacy-shielded, etc.)?

Data sources (no API key required):
  1. RDAP (Registration Data Access Protocol) via rdap.org bootstrap proxy.
     RDAP is the modern, structured, ICANN-mandated replacement for WHOIS
     and has broad gTLD + growing ccTLD coverage.
  2. Fallback: python-whois (raw WHOIS on port 43) for TLDs RDAP doesn't
     cover well (notably some ccTLDs such as .in).

Both are best-effort: registries rate-limit / block, and offer no SLA.
Failures degrade gracefully to "applicable: False" rather than crashing
the whole analysis.
"""

import re
import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Optional

import requests

try:
    import whois as _pywhois  # python-whois
except ImportError:  # pragma: no cover
    _pywhois = None


RDAP_TIMEOUT = 5    # seconds, per RDAP HTTP call
WHOIS_TIMEOUT = 5    # seconds, per WHOIS hop (a referral chain can have 2+ hops)
OVERALL_BUDGET = 8   # hard ceiling for the whole domain_age check, RDAP + WHOIS combined

# Thresholds (in days since registration)
VERY_NEW = 30
NEW = 90
RECENT = 180
ESTABLISHED = 365
MATURE = 730


def analyze(company_domain: str = "", contact_domain: str = "", text: str = "") -> dict:
    """
    Picks the most relevant domain to check: prefer contact_domain (the
    domain actually used to send the offer — the one most likely to be
    freshly stood up for a scam), fall back to company_domain, fall back
    to any email domain found in the raw text.
    """
    target = _pick_target_domain(company_domain, contact_domain, text)

    if not target:
        return {
            "domain_age": {
                "score": 5,
                "reason": "No domain available to check registration age",
                "applicable": False,
            }
        }

    record, source = _lookup_with_budget(target)

    if record is None or record.get("created") is None:
        return {
            "domain_age": {
                "score": 5,
                "reason": (
                    f"Could not retrieve registration data for '{target}' "
                    f"(registry unresponsive, rate-limited, or privacy-gated). "
                    f"Treat as unverified rather than clean."
                ),
                "applicable": False,
            }
        }

    return {"domain_age": _score_record(target, record, source)}


def _lookup_with_budget(target: str):
    """
    Runs RDAP-then-WHOIS in a worker thread with a hard overall deadline.
    Individual library timeouts (RDAP_TIMEOUT, WHOIS_TIMEOUT) are best-effort
    per hop; a multi-hop WHOIS referral chain can still exceed them in
    aggregate, so this is the actual guarantee that /analyze never stalls
    waiting on a slow or unresponsive registry.
    """
    def _do_lookup():
        record = _lookup_rdap(target)
        if record is not None:
            return record, "RDAP"
        if _pywhois is not None:
            record = _lookup_whois(target)
            if record is not None:
                return record, "WHOIS"
        return None, "none"

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do_lookup)
    try:
        return future.result(timeout=OVERALL_BUDGET)
    except FutureTimeoutError:
        return None, "timeout"
    finally:
        # wait=False: don't block returning to the caller on a thread that's
        # still stuck in a blocking socket call. It'll clean itself up once
        # the OS-level socket timeout eventually fires.
        executor.shutdown(wait=False)


def _pick_target_domain(company_domain: str, contact_domain: str, text: str) -> str:
    for d in (contact_domain, company_domain):
        if d:
            return _clean_domain(d)
    if text:
        m = re.findall(r"[\w.\-]+@([\w.\-]+\.\w+)", text)
        if m:
            return _clean_domain(m[0])
    return ""


def _clean_domain(domain: str) -> str:
    domain = domain.lower().strip()
    domain = re.sub(r"^(https?://)?(www\.)?", "", domain)
    domain = domain.split("/")[0]
    return domain


def _lookup_rdap(domain: str) -> Optional[dict]:
    try:
        resp = requests.get(f"https://rdap.org/domain/{domain}", timeout=RDAP_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    created = None
    updated = None
    expires = None
    privacy = False

    for event in data.get("events", []):
        action = event.get("eventAction", "")
        date_str = event.get("eventDate")
        if not date_str:
            continue
        try:
            dt = _parse_iso(date_str)
        except Exception:
            continue
        if action == "registration":
            created = dt
        elif action == "last changed":
            updated = dt
        elif action == "expiration":
            expires = dt

    # Detect privacy/proxy registrant
    for entity in data.get("entities", []):
        vcard = entity.get("vcardArray")
        if vcard and len(vcard) > 1:
            for field in vcard[1]:
                if isinstance(field, list) and len(field) >= 4:
                    val = str(field[3]).lower()
                    if "privacy" in val or "redacted" in val or "proxy" in val:
                        privacy = True

    if created is None:
        return None

    return {"created": created, "updated": updated, "expires": expires, "privacy": privacy}


def _lookup_whois(domain: str) -> Optional[dict]:
    try:
        w = _pywhois.whois(domain, timeout=WHOIS_TIMEOUT)
    except Exception:
        return None

    created = _first_date(w.creation_date)
    expires = _first_date(w.expiration_date)
    updated = _first_date(w.updated_date)

    if created is None:
        return None

    registrant = str(getattr(w, "org", "") or getattr(w, "registrant_name", "") or "").lower()
    privacy = any(k in registrant for k in ("privacy", "redacted", "proxy", "whoisguard"))

    return {"created": created, "updated": updated, "expires": expires, "privacy": privacy}


def _first_date(val):
    if isinstance(val, list):
        val = val[0] if val else None
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, str):
        try:
            return _parse_iso(val).replace(tzinfo=None)
        except Exception:
            return None
    return None


def _parse_iso(date_str: str) -> datetime.datetime:
    date_str = date_str.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(date_str)
    return dt.replace(tzinfo=None)


def _score_record(domain: str, record: dict, source: str) -> dict:
    created = record["created"]
    expires = record.get("expires")
    privacy = record.get("privacy", False)
    now = datetime.datetime.utcnow()
    age_days = (now - created).days

    notes = [f"registered {created.date().isoformat()} ({age_days} days ago, via {source})"]

    if age_days < 0:
        # clock skew / bad data
        score = 5
        notes.append("registration date is in the future — data likely unreliable")
    elif age_days < VERY_NEW:
        score = 0
        notes.append("CRITICAL: domain registered under 30 days ago — classic disposable scam-domain pattern")
    elif age_days < NEW:
        score = 1
        notes.append("domain registered under 3 months ago — very high risk for an established employer")
    elif age_days < RECENT:
        score = 3
        notes.append("domain registered under 6 months ago — still suspicious for a legitimate company")
    elif age_days < ESTABLISHED:
        score = 5
        notes.append("domain under 1 year old — treat with caution, corroborate with other signals")
    elif age_days < MATURE:
        score = 7
        notes.append("domain 1-2 years old — moderately established")
    else:
        score = 9
        notes.append("domain over 2 years old — consistent with an established organization")

    # Single-year registration horizon is itself a mild scam signal
    # (legitimate corporates often multi-year renew; throwaway domains
    # are typically registered for exactly the minimum term).
    if expires:
        reg_span_days = (expires - created).days
        if 0 < reg_span_days <= 370 and age_days < ESTABLISHED:
            score = max(0, score - 1)
            notes.append("registered for only ~1 year with no long-term renewal — consistent with a throwaway domain")

    if privacy and age_days < ESTABLISHED:
        score = max(0, score - 1)
        notes.append("WHOIS/RDAP privacy shielding active on a young domain — reduces traceability")
    elif privacy:
        notes.append("privacy shielding active (common and less concerning on an older, established domain)")

    return {
        "score": score,
        "reason": f"Domain '{domain}': " + "; ".join(notes),
        "applicable": True,
    }
